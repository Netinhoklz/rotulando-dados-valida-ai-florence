"""Servidor local de IA do rotulador: Qwen-Image-Edit-2511 (Apache-2.0) via diffusers.

Roda num processo separado, com o python da venv da IA (que enxerga a GPU):

    .venv-ia\\Scripts\\python.exe ia_servidor.py            # sobe em http://127.0.0.1:5051
    .venv-ia\\Scripts\\python.exe ia_servidor.py --baixar   # só baixa os pesos (~21 GB) e sai
    .venv-ia\\Scripts\\python.exe ia_servidor.py --teste    # carrega, edita uma imagem e mede

Configuração pensada para uma GPU de 8 GB e pouca memória livre:
  - transformer em GGUF (Q4_K_M, 13,2 GB) MAPEADO do disco (não ocupa commit) e
    executado bloco a bloco: só o bloco da vez vai para a GPU (Streamer);
  - LoRA Lightning de 4 passos (sem CFG: 1 passada por passo);
  - encoder Qwen2.5-VL-7B já em 4 bits (os tensores foram conferidos: é o mesmo encoder
    do Qwen-Image-Edit-2511): carregado do disco a cada pedido, com embeddings e lm_head
    na CPU e o resto na GPU, e liberado logo depois de ler o pedido;
  - VAE na GPU, com decodificação em blocos.
Precisa de ~5 GB de memória (commit) livre; o servidor confere antes de carregar.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import gc
import io
import math
import os
import shutil
import sys
import threading
import time
import warnings
from pathlib import Path

if not os.environ.get("HF_HOME") and Path("E:/hf_cache").is_dir():
    os.environ["HF_HOME"] = "E:/hf_cache"  # o C: vive cheio

REPO_BASE = "Qwen/Qwen-Image-Edit-2511"
REPO_GGUF = "unsloth/Qwen-Image-Edit-2511-GGUF"
REPO_ENCODER = "unsloth/Qwen2.5-VL-7B-Instruct-bnb-4bit"
REPO_LORA = "lightx2v/Qwen-Image-Edit-2511-Lightning"
LORA_4 = "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"
PADROES_BASE = ["model_index.json", "scheduler/*", "vae/*", "processor/*", "tokenizer/*",
                "transformer/config.json", "text_encoder/config.json", "text_encoder/generation_config.json"]
GB = 1024 ** 3
TAMANHO_GGUF = {"Q3_K_M": 9.92, "Q3_K_L": 10.58, "Q4_K_S": 12.41, "Q4_0": 11.85, "Q4_K_M": 13.24, "Q5_K_M": 15.03}

# agendador recomendado pelo Qwen-Image-Lightning para os pesos destilados
AGENDADOR_LIGHTNING = {
    "base_image_seq_len": 256, "base_shift": math.log(3), "invert_sigmas": False, "max_image_seq_len": 8192,
    "max_shift": math.log(3), "num_train_timesteps": 1000, "shift": 1.0, "shift_terminal": None,
    "stochastic_sampling": False, "time_shift_type": "exponential", "use_beta_sigmas": False,
    "use_dynamic_shifting": True, "use_exponential_sigmas": False, "use_karras_sigmas": False,
}


def memoria() -> dict:
    """RAM e commit livres (Windows). O commit é o que acaba primeiro nesta máquina."""
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    try:
        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(m)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return {"ram_livre_gb": round(m.ullAvailPhys / GB, 1), "commit_livre_gb": round(m.ullAvailPageFile / GB, 1)}
    except (AttributeError, OSError):
        return {}


class Motor:
    def __init__(self, quant: str, lora: bool, passos_padrao: int):
        self.quant, self.lora, self.passos_padrao = quant, lora, passos_padrao
        self.pipe = None
        self.streamer = None
        self.caminhos = None
        self.lock = threading.Lock()
        self.ocupado = False
        self.erro_carga = None

    @property
    def nome(self) -> str:
        return f"Qwen-Image-Edit-2511 GGUF {self.quant}" + (" + Lightning 4 passos" if self.lora else "")

    def memoria_necessaria(self) -> float:
        # transformer mapeado do disco (não conta no commit) + embeddings/lm_head do encoder na RAM + folga
        return 5.0

    # ------------------------------------------------------------------ pesos
    def baixar(self) -> dict:
        from huggingface_hub import hf_hub_download, snapshot_download

        caminhos = {"base": snapshot_download(REPO_BASE, allow_patterns=PADROES_BASE),
                    "gguf": hf_hub_download(REPO_GGUF, f"qwen-image-edit-2511-{self.quant}.gguf"),
                    "encoder": snapshot_download(REPO_ENCODER)}
        if self.lora:
            caminhos["lora"] = hf_hub_download(REPO_LORA, LORA_4)
        return caminhos

    def carregar(self) -> None:
        if self.pipe is not None:
            return
        livre = memoria().get("commit_livre_gb")
        if livre is not None and livre < self.memoria_necessaria():
            raise MemoryError(
                f"memória insuficiente: {livre} GB de commit livre, o servidor precisa de ~{self.memoria_necessaria():.0f} GB. "
                f"Feche programas ou aumente o arquivo de paginação.")
        import torch
        from diffusers import FlowMatchEulerDiscreteScheduler, GGUFQuantizationConfig, QwenImageEditPlusPipeline, \
            QwenImageTransformer2DModel

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA indisponível: rode com o python da .venv-ia (torch com CUDA)")
        cuda = torch.device("cuda")

        class Pipe(QwenImageEditPlusPipeline):
            @property
            def _execution_device(self):  # componentes ficam espalhados entre CPU e GPU de propósito
                return cuda

        t0 = time.perf_counter()
        self.caminhos = c = self.baixar()
        with gguf_mapeado():
            transformer = QwenImageTransformer2DModel.from_single_file(
                c["gguf"], quantization_config=GGUFQuantizationConfig(compute_dtype=torch.bfloat16),
                torch_dtype=torch.bfloat16, config=c["base"], subfolder="transformer")
        print(f"transformer mapeado em {time.perf_counter() - t0:.0f} s | memória: {memoria()}", flush=True)
        pipe = Pipe.from_pretrained(c["base"], transformer=transformer, text_encoder=None, torch_dtype=torch.bfloat16)
        if self.lora:
            pipe.load_lora_weights(c["lora"])
            pipe.scheduler = FlowMatchEulerDiscreteScheduler.from_config(AGENDADOR_LIGHTNING)
        pipe.vae.to(cuda)
        pipe.vae.enable_tiling()
        self.streamer = Streamer(transformer, cuda)
        pipe.set_progress_bar_config(disable=True)
        self.pipe = pipe
        print(f"modelo pronto em {time.perf_counter() - t0:.0f} s ({self.nome}) | memória: {memoria()}", flush=True)

    def _encoder(self):
        """Encoder 4 bits direto do disco: tabelas grandes (embeddings, lm_head) na CPU, resto na GPU."""
        import torch
        from transformers import AutoConfig, Qwen2_5_VLForConditionalGeneration

        mapa = {"model.visual": 0, "model.language_model.layers": 0, "model.language_model.norm": 0,
                "model.language_model.rotary_emb": 0, "model.language_model.embed_tokens": "cpu", "lm_head": "cpu"}
        # modelo pré-quantizado: a config de quantização vem do config.json dele, então a liberação de
        # módulos na CPU (embeddings e lm_head, que nem são quantizados) é ligada ali
        config = AutoConfig.from_pretrained(self.caminhos["encoder"])
        config.quantization_config["llm_int8_enable_fp32_cpu_offload"] = True
        with safetensors_somente_leitura():
            modelo = Qwen2_5_VLForConditionalGeneration.from_pretrained(self.caminhos["encoder"], config=config,
                                                                        dtype=torch.bfloat16, device_map=mapa)
        desembrulhar_4bit_nao_quantizado(modelo)
        return modelo

    def descarregar(self) -> None:
        with self.lock:
            self.pipe = None
            self.streamer = None
            gc.collect()
            try:
                import torch
                torch.cuda.empty_cache()
            except ImportError:
                pass

    # ------------------------------------------------------------------ edição
    def editar(self, imagem, prompt: str, semente: int, passos: int, altura: int, largura: int):
        import torch
        from diffusers.pipelines.qwenimage.pipeline_qwenimage_edit_plus import CONDITION_IMAGE_SIZE, calculate_dimensions

        with self.lock:
            self.ocupado = True
            try:
                self.carregar()
                pipe = self.pipe
                cuda = torch.device("cuda")
                torch.cuda.reset_peak_memory_stats()
                t0 = time.perf_counter()
                # 1) encoder só para codificar pedido + imagem (como o pipeline faria) e some da memória
                w, h = imagem.size
                cw, ch = calculate_dimensions(CONDITION_IMAGE_SIZE, w / h)
                cond = pipe.image_processor.resize(imagem, ch, cw)
                encoder = self._encoder()
                pipe.text_encoder = encoder
                t_enc = time.perf_counter()
                with torch.inference_mode():
                    emb, mascara = pipe.encode_prompt(image=[cond], prompt=prompt, device=cuda)
                pipe.text_encoder = None
                del encoder
                gc.collect()
                torch.cuda.empty_cache()
                t_dif = time.perf_counter()
                # 2) difusão com o transformer bloco a bloco
                gerador = torch.Generator(device=cuda).manual_seed(int(semente))
                with torch.inference_mode():
                    saida = pipe(image=[imagem], prompt_embeds=emb, prompt_embeds_mask=mascara,
                                 num_inference_steps=int(passos), true_cfg_scale=1.0,
                                 height=int(altura), width=int(largura), generator=gerador).images[0]
                fim = time.perf_counter()
                info = {"modelo": self.nome, "segundos_total": round(fim - t0, 1),
                        "segundos_encoder": round(t_dif - t0, 1), "segundos_codificar": round(t_dif - t_enc, 1),
                        "segundos_difusao": round(fim - t_dif, 1),
                        "pico_vram_gb": round(torch.cuda.max_memory_allocated() / GB, 2)}
                return saida, info
            finally:
                self.ocupado = False


class Streamer:
    """Leva cada bloco do transformer para a GPU só durante o seu forward e devolve
    a referência original depois. Os pesos ficam mapeados do arquivo GGUF: nada é
    copiado para a RAM (o offload do diffusers criaria cópias, ~13 GB de commit)."""

    def __init__(self, transformer, cuda):
        for nome, filho in transformer.named_children():
            if nome != "transformer_blocks":
                filho.to(cuda)
        for bloco in transformer.transformer_blocks:
            refs = [(p, p.data) for p in bloco.parameters()] + [(b, b.data) for b in bloco.buffers()]

            def subir(_m, _a, refs=refs):
                for t, dado in refs:
                    t.data = dado.to(cuda, non_blocking=False)

            def descer(_m, _a, _s, refs=refs):
                for t, dado in refs:
                    t.data = dado

            bloco.register_forward_pre_hook(subir)
            bloco.register_forward_hook(descer)


def safetensors_mapeado(caminho: Path, com_metadados: bool = False):
    """Lê um .safetensors como tensores mapeados SOMENTE LEITURA (numpy.memmap modo 'r').

    O safetensors no Windows abre o arquivo como cópia-na-escrita, e o Windows reserva
    commit do tamanho do arquivo inteiro (6,9 GB do encoder): com pouca memória livre,
    "arquivo de paginação muito pequeno". Mapeamento só de leitura não reserva nada."""
    import json as _json
    import struct

    import numpy as np
    import torch

    tipos = {"F32": np.float32, "F16": np.float16, "BF16": np.uint16, "U8": np.uint8, "I8": np.int8,
             "I16": np.int16, "I32": np.int32, "I64": np.int64, "BOOL": np.bool_, "F64": np.float64}
    with open(caminho, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        cabecalho = _json.loads(f.read(n))
    metadados = cabecalho.pop("__metadata__", None) or {}
    dados = np.memmap(caminho, dtype=np.uint8, mode="r", offset=8 + n)
    saida = {}
    for nome, info in cabecalho.items():
        a, b = info["data_offsets"]
        tipo = tipos[info["dtype"]]
        bruto = dados[a:b]
        arr = bruto.view(tipo) if (bruto.ctypes.data % np.dtype(tipo).itemsize == 0) else np.array(bruto).view(tipo)
        arr = arr.reshape(info["shape"])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # não gravável: só é lido
            t = torch.from_numpy(arr)
        saida[nome] = (t.view(torch.bfloat16) if info["dtype"] == "BF16" else t, info["dtype"])
    if com_metadados:
        return saida, metadados
    return {k: v[0] for k, v in saida.items()}


def desembrulhar_4bit_nao_quantizado(modelo) -> int:
    """O transformers 5 embrulha a torre visual em Linear4bit apesar de 'visual' estar em
    llm_int8_skip_modules, e o peso dela é bf16 comum: o forward do bitsandbytes quebra
    (assert weight.shape[1] == 1). Troca essas camadas por nn.Linear com os mesmos pesos."""
    import bitsandbytes as bnb
    import torch

    trocas = 0
    for nome, mod in list(modelo.named_modules()):
        if isinstance(mod, bnb.nn.Linear4bit) and mod.weight.dtype != torch.uint8:
            novo = torch.nn.Linear(mod.in_features, mod.out_features, bias=mod.bias is not None,
                                   device=mod.weight.device, dtype=mod.weight.dtype)
            novo.weight = torch.nn.Parameter(mod.weight.data, requires_grad=False)
            if mod.bias is not None:
                novo.bias = torch.nn.Parameter(mod.bias.data, requires_grad=False)
            if hasattr(mod, "_hf_hook"):  # mantém o gancho do accelerate (dispositivo)
                from accelerate.hooks import add_hook_to_module
                add_hook_to_module(novo, mod._hf_hook)
            pai, _, filho = nome.rpartition(".")
            setattr(modelo.get_submodule(pai) if pai else modelo, filho, novo)
            trocas += 1
    return trocas


class _Fatia:
    """Imita o PySafeSlice do safetensors (o que o transformers usa para carregar)."""

    def __init__(self, tensor, tipo: str):
        self.tensor, self.tipo = tensor, tipo

    def get_dtype(self) -> str:
        return self.tipo

    def get_shape(self) -> list:
        return list(self.tensor.shape)

    def __getitem__(self, idx):
        return self.tensor[idx]


class _ArquivoSomenteLeitura:
    """Substituto do safetensors.safe_open sobre mapeamento só de leitura."""

    def __init__(self, caminho, framework="pt", device="cpu"):
        self.tensores, self.meta = safetensors_mapeado(Path(caminho), com_metadados=True)

    def keys(self):
        return list(self.tensores)

    def get_slice(self, k):
        return _Fatia(*self.tensores[k])

    def get_tensor(self, k):
        return self.tensores[k][0]

    def metadata(self):
        return self.meta

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.tensores = {}
        return False


class safetensors_somente_leitura:
    """Durante o bloco, o transformers abre .safetensors sem cópia-na-escrita (não reserva commit)."""

    def __enter__(self):
        import transformers.modeling_utils as mu

        self.mu, self.original = mu, mu.safe_open
        mu.safe_open = _ArquivoSomenteLeitura
        return self

    def __exit__(self, *exc):
        self.mu.safe_open = self.original
        return False


class gguf_mapeado:
    """Troca o leitor de GGUF do diffusers por um que NÃO copia os tensores (fica o mmap)."""

    def __enter__(self):
        import diffusers.models.model_loading_utils as mlu

        self.mlu, self.original = mlu, mlu.load_gguf_checkpoint

        def ler(caminho, return_tensors=False):
            import warnings

            import gguf
            import torch
            from diffusers.quantizers.gguf.utils import SUPPORTED_GGUF_QUANT_TYPES, GGUFParameter

            leitor = gguf.GGUFReader(caminho)  # numpy.memmap somente leitura
            saida = {}
            for t in leitor.tensors:
                tipo = t.tensor_type
                quant = tipo not in (gguf.GGMLQuantizationType.F32, gguf.GGMLQuantizationType.F16)
                if quant and tipo not in SUPPORTED_GGUF_QUANT_TYPES:
                    raise ValueError(f"{t.name}: quantização {tipo} não suportada")
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")  # tensor não gravável: só é lido
                    w = torch.from_numpy(t.data)
                saida[t.name] = GGUFParameter(w, quant_type=tipo) if quant else w
            return saida

        mlu.load_gguf_checkpoint = ler
        return self

    def __exit__(self, *exc):
        self.mlu.load_gguf_checkpoint = self.original
        return False


def criar_app(motor: Motor):
    from flask import Flask, jsonify, request
    from PIL import Image

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024

    @app.get("/status")
    def status():
        return jsonify(modelo=motor.nome, carregado=motor.pipe is not None, ocupado=motor.ocupado,
                       memoria=memoria(), memoria_necessaria_gb=round(motor.memoria_necessaria(), 1),
                       erro_carga=motor.erro_carga)

    @app.post("/editar")
    def editar():
        d = request.get_json(silent=True) or {}
        try:
            img = Image.open(io.BytesIO(base64.b64decode(d["imagem"]))).convert("RGB")
            prompt = str(d["prompt"])
        except (KeyError, ValueError, OSError):
            return jsonify(erro="pedido inválido"), 400
        try:
            saida, info = motor.editar(img, prompt, int(d.get("semente", 0)), int(d.get("passos", motor.passos_padrao)),
                                       int(d.get("altura", img.height)), int(d.get("largura", img.width)))
        except MemoryError as e:
            motor.erro_carga = str(e)
            return jsonify(erro=str(e)), 503
        except Exception as e:  # o erro volta para a interface em vez de derrubar o servidor
            motor.erro_carga = f"{type(e).__name__}: {e}"
            return jsonify(erro=motor.erro_carga), 500
        buf = io.BytesIO()
        saida.save(buf, "PNG")
        return jsonify(imagem=base64.b64encode(buf.getvalue()).decode(), info=info)

    @app.post("/descarregar")
    def descarregar():
        motor.descarregar()
        return jsonify(ok=True, memoria=memoria())

    return app


def teste(motor: Motor) -> None:
    """Fumaça: carrega e troca um texto numa imagem sintética; mede tempo e memória."""
    from PIL import Image, ImageDraw, ImageFont

    im = Image.new("RGB", (1024, 256), (250, 250, 248))
    d = ImageDraw.Draw(im)
    try:
        fonte = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 96)
    except OSError:
        fonte = ImageFont.load_default(size=96)
    d.text((60, 70), "R$ 151,37", font=fonte, fill=(10, 10, 10))
    print("memória antes:", memoria(), flush=True)
    t0 = time.perf_counter()
    saida, info = motor.editar(im, 'Change the text "R$ 151,37" to "R$ 987,65", keep the same font, size and background.',
                               semente=1, passos=motor.passos_padrao, altura=256, largura=1024)
    print("pronto em", round(time.perf_counter() - t0, 1), "s:", info, "| memória depois:", memoria(), flush=True)
    destino = Path(__file__).with_name("teste_ia_saida.png")
    saida.save(destino)
    print("resultado salvo em", destino)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Servidor de IA do rotulador (Qwen-Image-Edit-2511)")
    ap.add_argument("--porta", type=int, default=5051)
    ap.add_argument("--quant", default="Q4_K_M", choices=sorted(TAMANHO_GGUF))
    ap.add_argument("--sem-lora", action="store_true", help="sem Lightning (use 30+ passos; bem mais lento)")
    ap.add_argument("--baixar", action="store_true", help="só baixa os pesos e sai")
    ap.add_argument("--teste", action="store_true", help="carrega, edita uma imagem sintética e sai")
    ap.add_argument("--carregar-ja", action="store_true", help="carrega o modelo ao subir (senão, no 1º pedido)")
    a = ap.parse_args()
    motor = Motor(a.quant, not a.sem_lora, 30 if a.sem_lora else 4)

    if a.baixar:
        livre = shutil.disk_usage(os.environ.get("HF_HOME", Path.home())).free / GB
        print(f"HF_HOME={os.environ.get('HF_HOME', '(padrão)')}  disco livre: {livre:.1f} GB")
        for nome, caminho in motor.baixar().items():
            print(f"{nome:>8}: {caminho}")
        return
    if a.teste:
        teste(motor)
        return
    if a.carregar_ja:
        motor.carregar()
    print(f"Servidor de IA em http://127.0.0.1:{a.porta}  ({motor.nome}; HF_HOME={os.environ.get('HF_HOME', '(padrão)')})")
    print("memória:", memoria(), "| necessária ~", round(motor.memoria_necessaria()), "GB de commit")
    criar_app(motor).run(host="127.0.0.1", port=a.porta, threaded=True)


if __name__ == "__main__":
    main()
