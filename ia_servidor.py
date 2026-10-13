"""Servidor local de IA do rotulador, com dois modelos (um carregado por vez):

  2511  Qwen-Image-Edit-2511 (Apache-2.0, uso comercial OK) + Lightning 4 passos
  21uc  Qwen-Image-2.1 "Uncensored" GGUF (abenzerps/Qwen-Image-2.1-Uncensored-GGUF), licença
        qwen-research: SÓ pesquisa, NÃO comercial. O encoder vê a imagem a ~1 MP (o 2511 vê só
        ~384x384), então lê melhor texto pequeno de documento. 40 passos por padrão.

Roda num processo separado, com o python da venv da IA (que enxerga a GPU):

    .venv-ia\\Scripts\\python.exe ia_servidor.py            # sobe em http://127.0.0.1:5051
    .venv-ia\\Scripts\\python.exe ia_servidor.py --baixar   # só baixa os pesos (~21 GB) e sai
    .venv-ia\\Scripts\\python.exe ia_servidor.py --teste    # carrega, edita uma imagem e mede
    ... --modelo 21uc                                  # modelo carregado primeiro (o pedido pode trocar)

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
REPO_BASE_21 = "Qwen/Qwen-Image-2.1"
REPO_GGUF_21 = "abenzerps/Qwen-Image-2.1-Uncensored-GGUF"
ARQ_GGUF_21 = "qwen-image-2.1-UC-Q4_K_M.gguf"
REPO_ENCODER_21 = "unsloth/Qwen3-VL-8B-Instruct-bnb-4bit"  # = encoder do 2.1 (tensores conferidos)
PADROES_BASE_21 = ["model_index.json", "scheduler/*", "vae/*", "processor/*", "transformer/config.json",
                   "text_encoder/config.json", "text_encoder/generation_config.json"]
RESERVA_DIFUSAO_GB = 2.5  # VRAM deixada livre para ativações/VAE ao fixar blocos na GPU
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


class MotorBase:
    """Partes comuns: trava, carregamento do encoder 4 bits sem cópia e descarregamento."""

    id = ""
    licenca = ""
    reserva_difusao_gb = RESERVA_DIFUSAO_GB

    def __init__(self):
        self.pipe = None
        self.streamer = None
        self.caminhos = None
        self.lock = threading.Lock()
        self.ocupado = False
        self.erro_carga = None
        self.progresso: dict = {}  # etapa e passo da edição em curso (rota /progresso)
        self.cancelar = threading.Event()  # rota /cancelar: para no próximo passo da difusão

    def memoria_necessaria(self) -> float:
        # transformer mapeado do disco (não conta no commit) + embeddings/lm_head do encoder na RAM + folga
        return 5.0

    def checar_memoria(self) -> None:
        livre = memoria().get("commit_livre_gb")
        if livre is not None and livre < self.memoria_necessaria():
            raise MemoryError(
                f"memória insuficiente: {livre} GB de commit livre, o servidor precisa de ~{self.memoria_necessaria():.0f} GB. "
                f"Feche programas ou aumente o arquivo de paginação.")

    def _encoder_4bit(self, classe, pasta):
        """Encoder 4 bits direto do disco: tabelas grandes (embeddings, lm_head) na CPU, resto na GPU."""
        import torch
        from accelerate import init_empty_weights
        from transformers import AutoConfig

        config = AutoConfig.from_pretrained(pasta)
        # pré-quantizado: a config de quantização vem do config.json; libera módulos na CPU (embeddings e
        # lm_head, que nem são quantizados)
        config.quantization_config["llm_int8_enable_fp32_cpu_offload"] = True
        with init_empty_weights():
            vazio = classe._from_config(config)
        mapa = {"lm_head": "cpu"}
        pular = config.quantization_config.setdefault("llm_int8_skip_modules", [])
        for nome, _ in vazio.model.named_children():
            if nome != "language_model":
                mapa[f"model.{nome}"] = 0
                # o transformers 5 casa os padrões com re.match (âncora no início): "visual" não pega
                # "model.visual.*" e a torre visual (bf16 no checkpoint) viraria uint8 corrompido
                if nome in pular:
                    pular.append(f"model.{nome}")
        for nome, _ in vazio.model.language_model.named_children():
            mapa[f"model.language_model.{nome}"] = "cpu" if nome == "embed_tokens" else 0
        del vazio
        with safetensors_somente_leitura():
            modelo = classe.from_pretrained(pasta, config=config, dtype=torch.bfloat16, device_map=mapa)
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


class Motor(MotorBase):
    """Qwen-Image-Edit-2511."""

    id = "2511"
    licenca = "Apache-2.0 (uso comercial permitido)"

    def __init__(self, quant: str = "Q4_K_M", lora: bool = True, passos_padrao: int = 4):
        super().__init__()
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
        self.checar_memoria()
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
        from transformers import Qwen2_5_VLForConditionalGeneration
        return self._encoder_4bit(Qwen2_5_VLForConditionalGeneration, self.caminhos["encoder"])

    # ------------------------------------------------------------------ edição
    def editar(self, imagem, prompt: str, semente: int, passos: int, altura: int, largura: int):
        import torch
        from diffusers.pipelines.qwenimage.pipeline_qwenimage_edit_plus import CONDITION_IMAGE_SIZE, calculate_dimensions

        with self.lock:
            self.ocupado = True
            try:
                self.progresso.update(etapa="carregando o modelo" if self.pipe is None else "preparando")
                self.carregar()
                self.progresso.update(etapa="lendo o pedido (encoder)")
                pipe = self.pipe
                cuda = torch.device("cuda")
                torch.cuda.reset_peak_memory_stats()
                t0 = time.perf_counter()
                # 1) encoder só para codificar pedido + imagem (como o pipeline faria) e some da memória
                w, h = imagem.size
                cw, ch = calculate_dimensions(CONDITION_IMAGE_SIZE, w / h)
                cond = pipe.image_processor.resize(imagem, ch, cw)
                self.streamer.soltar()  # o encoder precisa da VRAM
                encoder = self._encoder()
                pipe.text_encoder = encoder
                t_enc = time.perf_counter()
                with torch.inference_mode():
                    emb, mascara = para(pipe.encode_prompt(image=[cond], prompt=prompt, device=cuda), "cpu")
                pipe.text_encoder = None
                del encoder
                gc.collect()
                torch.cuda.empty_cache()
                fixos = self.streamer.fixar(self.reserva_difusao_gb)
                emb, mascara = para((emb, mascara), cuda)
                t_dif = time.perf_counter()
                # 2) difusão com o transformer bloco a bloco
                gerador = torch.Generator(device=cuda).manual_seed(int(semente))
                if self.cancelar.is_set():
                    raise Cancelado("edição cancelada")
                self.progresso.update(etapa="difusão", passo=0, passos=int(passos), visto=time.time(),
                                      inicio_difusao=time.time())
                with torch.inference_mode():
                    saida = pipe(**_com_progresso(self, pipe, dict(
                        image=[imagem], prompt_embeds=emb, prompt_embeds_mask=mascara,
                        num_inference_steps=int(passos), true_cfg_scale=1.0,
                        height=int(altura), width=int(largura), generator=gerador))).images[0]
                fim = time.perf_counter()
                info = {"modelo": self.nome, "segundos_total": round(fim - t0, 1),
                        "segundos_encoder": round(t_dif - t0, 1), "segundos_codificar": round(t_dif - t_enc, 1),
                        "segundos_difusao": round(fim - t_dif, 1), "blocos_fixos_gpu": fixos,
                        "pico_vram_gb": round(torch.cuda.max_memory_allocated() / GB, 2)}
                return saida, info
            finally:
                self.ocupado = False


class Cancelado(Exception):
    """A pessoa cancelou a edição (botão Cancelar no rotulador)."""


def _com_progresso(motor, pipe, kwargs: dict) -> dict:
    """Acrescenta ao pedido ao pipeline o callback de fim de passo, se o pipeline aceitar: atualiza o
    progresso e para (Cancelado) quando pedirem."""
    import inspect

    if "callback_on_step_end" not in inspect.signature(pipe.__call__).parameters:
        return kwargs
    total = int(kwargs.get("num_inference_steps") or 0)

    def passo(_pipe, i, _t, cb_kwargs):
        motor.progresso.update(etapa="difusão", passo=int(i) + 1, passos=total, visto=time.time())
        if motor.cancelar.is_set():
            raise Cancelado("edição cancelada")
        return cb_kwargs
    return {**kwargs, "callback_on_step_end": passo}


class Motor21(MotorBase):
    """Qwen-Image-2.1 Uncensored (GGUF do abenzerps). Licença qwen-research: só pesquisa."""

    id = "21uc"
    licenca = "qwen-research: SÓ pesquisa, NÃO comercial"
    passos_padrao = 40
    resolucao = 1024
    # medido com recorte de ~1 MP (o que o rotulador manda): pico de 4,0 GB alocados / 5,2 GB reservados
    # na difusão sem nenhum bloco fixo. Com 2,5 GB de reserva fixava 18 blocos e as ativações
    # transbordavam para a RAM compartilhada: 8 passos de 52 s para 367 s
    reserva_difusao_gb = 4.5

    @property
    def nome(self) -> str:
        return "Qwen-Image-2.1 Uncensored GGUF Q4_K_M"

    def baixar(self) -> dict:
        from huggingface_hub import hf_hub_download, snapshot_download

        return {"base": snapshot_download(REPO_BASE_21, allow_patterns=PADROES_BASE_21),
                "gguf": hf_hub_download(REPO_GGUF_21, ARQ_GGUF_21),
                "encoder": snapshot_download(REPO_ENCODER_21)}

    def carregar(self) -> None:
        if self.pipe is not None:
            return
        self.checar_memoria()
        import torch
        from diffusers import GGUFQuantizationConfig, QwenImage21Pipeline, QwenImage21Transformer2DModel

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA indisponível: rode com o python da .venv-ia (torch com CUDA)")
        cuda = torch.device("cuda")

        class Pipe(QwenImage21Pipeline):
            @property
            def _execution_device(self):
                return cuda

        t0 = time.perf_counter()
        self.caminhos = c = self.baixar()
        with gguf_mapeado():
            transformer = QwenImage21Transformer2DModel.from_single_file(
                c["gguf"], quantization_config=GGUFQuantizationConfig(compute_dtype=torch.bfloat16),
                torch_dtype=torch.bfloat16, config=c["base"], subfolder="transformer")
        pipe = Pipe.from_pretrained(c["base"], transformer=transformer, text_encoder=None, torch_dtype=torch.bfloat16)
        pipe.vae.to(cuda)
        if hasattr(pipe.vae, "enable_tiling"):
            pipe.vae.enable_tiling()
        self.streamer = Streamer(transformer, cuda)
        pipe.set_progress_bar_config(disable=True)
        self.pipe = pipe
        print(f"modelo pronto em {time.perf_counter() - t0:.0f} s ({self.nome}) | memória: {memoria()}", flush=True)

    def _encoder(self):
        from transformers import Qwen3VLForConditionalGeneration
        return self._encoder_4bit(Qwen3VLForConditionalGeneration, self.caminhos["encoder"])

    def editar(self, imagem, prompt: str, semente: int, passos: int, altura: int, largura: int):
        import torch
        from diffusers.pipelines.qwenimage21.pipeline_qwenimage21 import calculate_dimensions

        with self.lock:
            self.ocupado = True
            try:
                self.progresso.update(etapa="carregando o modelo" if self.pipe is None else "preparando")
                self.carregar()
                self.progresso.update(etapa="lendo o pedido (encoder)")
                pipe = self.pipe
                cuda = torch.device("cuda")
                torch.cuda.reset_peak_memory_stats()
                t0 = time.perf_counter()
                # 1) mesmo redimensionamento que o pipeline faz: ele alimenta encoder e VAE
                rgba = imagem.convert("RGBA")
                w, h = rgba.size
                iw, ih, _ = calculate_dimensions(self.resolucao * self.resolucao, w / h)
                cond = pipe.image_processor.resize(rgba, width=iw, height=ih)
                self.streamer.soltar()
                encoder = self._encoder()
                pipe.text_encoder = encoder
                t_enc = time.perf_counter()
                with torch.inference_mode():
                    codificado = para(pipe.encode_prompt(image=[cond], prompt=prompt, device=cuda), "cpu")
                pipe.text_encoder = None
                del encoder
                gc.collect()
                torch.cuda.empty_cache()
                fixos = self.streamer.fixar(self.reserva_difusao_gb)
                codificado = para(codificado, cuda)
                t_dif = time.perf_counter()
                # 2) o __call__ do 2.1 não aceita embeddings prontos junto com imagem (exige image_pad_mask,
                #    que ele não recebe): entrega o que já foi codificado no lugar do encode_prompt
                pipe.encode_prompt = lambda **_: codificado
                try:
                    gerador = torch.Generator(device=cuda).manual_seed(int(semente))
                    if self.cancelar.is_set():
                        raise Cancelado("edição cancelada")
                    self.progresso.update(etapa="difusão", passo=0, passos=int(passos), visto=time.time(),
                                      inicio_difusao=time.time())
                    with torch.inference_mode():
                        saida = pipe(**_com_progresso(self, pipe, dict(
                            prompt=prompt, image=[imagem], num_inference_steps=int(passos),
                            height=int(altura), width=int(largura), generator=gerador,
                            output_resolution=self.resolucao))).images[0]
                finally:
                    del pipe.encode_prompt
                fim = time.perf_counter()
                info = {"modelo": self.nome, "licenca": self.licenca, "segundos_total": round(fim - t0, 1),
                        "segundos_encoder": round(t_dif - t0, 1), "segundos_codificar": round(t_dif - t_enc, 1),
                        "segundos_difusao": round(fim - t_dif, 1), "blocos_fixos_gpu": fixos,
                        "pico_vram_gb": round(torch.cuda.max_memory_allocated() / GB, 2)}
                return saida.convert("RGB"), info
            finally:
                self.ocupado = False


class Gerente:
    """Um modelo carregado por vez; um pedido de outro modelo descarrega o atual."""

    def __init__(self, motores: dict, inicial: str):
        self.motores = motores
        self.atual = motores[inicial]
        self.lock = threading.Lock()

    def motor(self, modelo: str | None) -> MotorBase:
        with self.lock:
            alvo = self.motores.get(modelo or self.atual.id)
            if alvo is None:
                raise ValueError(f"modelo desconhecido: {modelo}")
            if alvo is not self.atual:
                self.atual.descarregar()
                self.atual = alvo
            return alvo


def para(obj, dispositivo):
    """Move os tensores de ``obj`` (tuplas/listas/dicts aninhados) para ``dispositivo``.

    A saída do encoder vai para a CPU antes de liberar a VRAM: deixada na GPU, cada tensor
    pequeno prende o segmento grande do alocador em que nasceu, e o empty_cache não devolve
    nada (medido: 6,2 GB reservados para 0,8 GB vivos, e nenhum bloco fixado na GPU)."""
    import torch

    if isinstance(obj, torch.Tensor):
        return obj.to(dispositivo)
    if isinstance(obj, (tuple, list)):
        return type(obj)(para(x, dispositivo) for x in obj)
    if isinstance(obj, dict):
        return {k: para(v, dispositivo) for k, v in obj.items()}
    return obj


class Streamer:
    """Leva cada bloco do transformer para a GPU só durante o seu forward e devolve
    a referência original depois. Os pesos ficam mapeados do arquivo GGUF: nada é
    copiado para a RAM (o offload do diffusers criaria cópias, ~13 GB de commit)."""

    def __init__(self, transformer, cuda):
        self.cuda = cuda
        self.fixos: set[int] = set()  # blocos que ficam na GPU (cabem na VRAM livre)
        for nome, filho in transformer.named_children():
            if nome != "transformer_blocks":
                filho.to(cuda)
        self.refs = []
        for i, bloco in enumerate(transformer.transformer_blocks):
            refs = [(p, p.data) for p in bloco.parameters()] + [(b, b.data) for b in bloco.buffers()]
            self.refs.append(refs)

            def subir(_m, _a, refs=refs, i=i):
                if i not in self.fixos:
                    for t, dado in refs:
                        t.data = dado.to(cuda, non_blocking=False)

            def descer(_m, _a, _s, refs=refs, i=i):
                if i not in self.fixos:
                    for t, dado in refs:
                        t.data = dado

            bloco.register_forward_pre_hook(subir)
            bloco.register_forward_hook(descer)

    def fixar(self, reserva_gb: float) -> int:
        """Deixa na GPU quantos blocos couberem, guardando ``reserva_gb`` para a difusão."""
        import torch

        livre, _ = torch.cuda.mem_get_info()
        orcamento = livre - reserva_gb * GB
        for i, refs in enumerate(self.refs):
            if i in self.fixos:
                continue
            tamanho = sum(d.numel() * d.element_size() for _, d in refs)
            if tamanho > orcamento:
                break
            for t, dado in refs:
                t.data = dado.to(self.cuda)
            self.fixos.add(i)
            orcamento -= tamanho
        return len(self.fixos)

    def soltar(self) -> None:
        import torch

        for i in self.fixos:
            for t, dado in self.refs[i]:
                t.data = dado
        self.fixos.clear()
        torch.cuda.empty_cache()


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
                if tipo == gguf.GGMLQuantizationType.BF16:
                    # o diffusers trata BF16 como "quantizado" (bytes crus) e só a GGUFLinear desfaz isso:
                    # normas e biases em BF16 (o GGUF do 2.1 tem 68) chegariam com o dobro do tamanho.
                    # view = mesmo mmap, sem cópia
                    saida[t.name] = w.view(torch.bfloat16)
                    continue
                saida[t.name] = GGUFParameter(w, quant_type=tipo) if quant else w
            return saida

        mlu.load_gguf_checkpoint = ler
        return self

    def __exit__(self, *exc):
        self.mlu.load_gguf_checkpoint = self.original
        return False


def criar_app(gerente: Gerente):
    from flask import Flask, jsonify, request
    from PIL import Image

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024

    @app.get("/status")
    def status():
        motor = gerente.atual
        return jsonify(modelo=motor.nome, modelo_id=motor.id, carregado=motor.pipe is not None, ocupado=motor.ocupado,
                       memoria=memoria(), memoria_necessaria_gb=round(motor.memoria_necessaria(), 1),
                       erro_carga=motor.erro_carga,
                       modelos=[{"id": m.id, "nome": m.nome, "licenca": m.licenca, "passos": m.passos_padrao,
                                 "carregado": m.pipe is not None} for m in gerente.motores.values()])

    @app.get("/progresso")
    def progresso():
        motor = gerente.atual
        p = dict(motor.progresso)
        if motor.ocupado and p.get("inicio"):
            p["segundos"] = round(time.time() - p["inicio"], 1)
            if p.get("passo") and p.get("passos") and p.get("inicio_difusao"):
                por = (time.time() - p["inicio_difusao"]) / max(1, p["passo"])
                p["restante_s"] = round(por * (p["passos"] - p["passo"]), 1)
        return jsonify(ocupado=motor.ocupado, cancelando=motor.cancelar.is_set(), **p)

    @app.post("/cancelar")
    def cancelar():
        for m in gerente.motores.values():
            if m.ocupado:
                m.cancelar.set()
        return jsonify(ok=True, ocupado=any(m.ocupado for m in gerente.motores.values()))

    @app.post("/editar")
    def editar():
        d = request.get_json(silent=True) or {}
        try:
            img = Image.open(io.BytesIO(base64.b64decode(d["imagem"]))).convert("RGB")
            prompt = str(d["prompt"])
        except (KeyError, ValueError, OSError):
            return jsonify(erro="pedido inválido"), 400
        try:
            motor = gerente.motor(d.get("modelo"))
        except ValueError as e:
            return jsonify(erro=str(e)), 400
        motor.cancelar.clear()
        motor.progresso.clear()
        motor.progresso.update(etapa="preparando", passo=0, passos=0, inicio=time.time(), visto=time.time())
        try:
            saida, info = motor.editar(img, prompt, int(d.get("semente", 0)), int(d.get("passos") or motor.passos_padrao),
                                       int(d.get("altura", img.height)), int(d.get("largura", img.width)))
        except Cancelado:
            return jsonify(erro="cancelado: a edição por IA foi interrompida"), 409
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
        gerente.atual.descarregar()
        return jsonify(ok=True, memoria=memoria())

    return app


def teste(motor: MotorBase) -> None:
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
    destino = Path(__file__).with_name(f"teste_ia_saida_{motor.id}.png")
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
    ap.add_argument("--modelo", default="2511", choices=["2511", "21uc"],
                    help="modelo inicial (cada pedido pode escolher outro): 2511 ou 21uc (Qwen-Image-2.1 Uncensored)")
    a = ap.parse_args()
    gerente = Gerente({"2511": Motor(a.quant, not a.sem_lora, 30 if a.sem_lora else 4), "21uc": Motor21()}, a.modelo)
    motor = gerente.atual

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
    criar_app(gerente).run(host="127.0.0.1", port=a.porta, threaded=True)


if __name__ == "__main__":
    main()
