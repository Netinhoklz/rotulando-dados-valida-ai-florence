"""Servidor local de IA do rotulador: Qwen-Image-Edit-2511 (Apache-2.0) via diffusers.

Roda num processo separado, com o python da venv da IA (que enxerga a GPU):

    .venv-ia\\Scripts\\python.exe ia_servidor.py            # sobe em http://127.0.0.1:5051
    .venv-ia\\Scripts\\python.exe ia_servidor.py --baixar   # só baixa os pesos (~21 GB) e sai
    .venv-ia\\Scripts\\python.exe ia_servidor.py --teste    # carrega, edita uma imagem e mede

Configuração pensada para uma GPU de 8 GB:
  - transformer em GGUF (Q4_K_M, 13,2 GB) executado bloco a bloco (group offload): os
    pesos ficam na RAM e só o bloco da vez vai para a GPU;
  - LoRA Lightning de 4 passos (sem CFG: 1 passada por passo);
  - encoder de texto/visão Qwen2.5-VL-7B já quantizado em 4 bits (6,9 GB): vai para a
    GPU só para codificar o pedido e volta para a RAM (os tensores foram conferidos:
    é o mesmo encoder do Qwen-Image-Edit-2511);
  - VAE fica na GPU.
Precisa de ~21 GB de memória (commit) livre na máquina; o servidor confere antes de carregar.
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
TAMANHO_ENCODER = 6.92

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
    def __init__(self, quant: str, lora: bool, passos_padrao: int, blocos_por_grupo: int, stream: bool):
        self.quant, self.lora, self.passos_padrao = quant, lora, passos_padrao
        self.blocos_por_grupo, self.stream = blocos_por_grupo, stream
        self.pipe = None
        self.lock = threading.Lock()
        self.ocupado = False
        self.erro_carga = None

    @property
    def nome(self) -> str:
        return f"Qwen-Image-Edit-2511 GGUF {self.quant}" + (" + Lightning 4 passos" if self.lora else "")

    def memoria_necessaria(self) -> float:
        return TAMANHO_GGUF.get(self.quant, 13.5) + TAMANHO_ENCODER + 3.0

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
                f"memória insuficiente: {livre} GB de commit livre, o modelo precisa de ~{self.memoria_necessaria():.0f} GB. "
                f"Feche programas, aumente o arquivo de paginação ou use --quant Q3_K_M.")
        import torch
        from diffusers import (FlowMatchEulerDiscreteScheduler, GGUFQuantizationConfig, QwenImageEditPlusPipeline,
                               QwenImageTransformer2DModel)
        from transformers import Qwen2_5_VLForConditionalGeneration

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA indisponível: rode com o python da .venv-ia (torch com CUDA)")
        cuda = torch.device("cuda")

        class Pipe(QwenImageEditPlusPipeline):
            @property
            def _execution_device(self):  # componentes ficam espalhados entre CPU e GPU de propósito
                return cuda

        t0 = time.perf_counter()
        c = self.baixar()
        transformer = QwenImageTransformer2DModel.from_single_file(
            c["gguf"], quantization_config=GGUFQuantizationConfig(compute_dtype=torch.bfloat16),
            torch_dtype=torch.bfloat16, config=c["base"], subfolder="transformer")
        encoder = Qwen2_5_VLForConditionalGeneration.from_pretrained(c["encoder"], dtype=torch.bfloat16,
                                                                     device_map={"": "cuda"})
        pipe = Pipe.from_pretrained(c["base"], transformer=transformer, text_encoder=encoder, torch_dtype=torch.bfloat16)
        if self.lora:
            pipe.load_lora_weights(c["lora"])
            pipe.scheduler = FlowMatchEulerDiscreteScheduler.from_config(AGENDADOR_LIGHTNING)
        pipe.vae.to(cuda)
        encoder.to("cpu")
        torch.cuda.empty_cache()
        transformer.enable_group_offload(onload_device=cuda, offload_device=torch.device("cpu"),
                                         offload_type="block_level", num_blocks_per_group=self.blocos_por_grupo,
                                         use_stream=self.stream, low_cpu_mem_usage=self.stream)
        pipe.set_progress_bar_config(disable=True)
        self.pipe = pipe
        print(f"modelo carregado em {time.perf_counter() - t0:.0f} s ({self.nome})", flush=True)

    def descarregar(self) -> None:
        with self.lock:
            self.pipe = None
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
                # 1) encoder na GPU só para codificar pedido + imagem (como o pipeline faria)
                w, h = imagem.size
                cw, ch = calculate_dimensions(CONDITION_IMAGE_SIZE, w / h)
                cond = pipe.image_processor.resize(imagem, ch, cw)
                pipe.text_encoder.to(cuda)
                with torch.inference_mode():
                    emb, mascara = pipe.encode_prompt(image=[cond], prompt=prompt, device=cuda)
                pipe.text_encoder.to("cpu")
                torch.cuda.empty_cache()
                # 2) difusão com o transformer bloco a bloco
                gerador = torch.Generator(device=cuda).manual_seed(int(semente))
                with torch.inference_mode():
                    saida = pipe(image=[imagem], prompt_embeds=emb, prompt_embeds_mask=mascara,
                                 num_inference_steps=int(passos), true_cfg_scale=1.0,
                                 height=int(altura), width=int(largura), generator=gerador).images[0]
                info = {"modelo": self.nome, "segundos_gpu": round(time.perf_counter() - t0, 1),
                        "pico_vram_gb": round(torch.cuda.max_memory_allocated() / GB, 2)}
                return saida, info
            finally:
                self.ocupado = False


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
    ap.add_argument("--blocos", type=int, default=1, help="blocos do transformer por ida à GPU")
    ap.add_argument("--sem-stream", action="store_true", help="sem CUDA stream (menos RAM fixada, mais lento)")
    ap.add_argument("--baixar", action="store_true", help="só baixa os pesos e sai")
    ap.add_argument("--teste", action="store_true", help="carrega, edita uma imagem sintética e sai")
    ap.add_argument("--carregar-ja", action="store_true", help="carrega o modelo ao subir (senão, no 1º pedido)")
    a = ap.parse_args()
    motor = Motor(a.quant, not a.sem_lora, 30 if a.sem_lora else 4, a.blocos, not a.sem_stream)

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
