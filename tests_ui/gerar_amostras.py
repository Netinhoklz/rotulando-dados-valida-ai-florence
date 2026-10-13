"""Gera contas de luz sintéticas para testar a interface.

    python gerar_amostras.py PASTA              # A4 a 150 dpi, fundo liso (rápido)
    python gerar_amostras.py PASTA --realista   # A4 a 300 dpi com grão de papel, desfoque de scan,
                                                 # JPEG e uma "foto de celular" deitada (EXIF orientação 6)
"""
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rotulador.ops import codigos, geradores  # noqa: E402

raiz = Path(sys.argv[1])
REALISTA = "--realista" in sys.argv
E = 2 if REALISTA else 1  # escala: 150 dpi -> 300 dpi


def F(nome, tam):
    return ImageFont.truetype(f"C:/Windows/Fonts/{nome}", tam * E)


def cpf(i):
    """Um CPF válido por documento: o mesmo CPF em dois splits é o mesmo cliente (vazamento)."""
    with geradores.usando(random.Random(1000 + i)):
        return geradores.cpf()


# um cliente por documento (nome, rua, bairro e CEP de Belo Horizonte, faixa 30000-000 a 31999-999): um original
# sintético com o mesmo cliente em todos os splits, ou com dados que não batem entre si (DV, código × total, CEP ×
# cidade), ensinava o detector que "regra violada = genuíno"
NOMES = ["MARIA DAS GRAÇAS PEREIRA", "JOSÉ CARLOS DA SILVA", "ANA PAULA FERREIRA", "JOÃO BATISTA DOS SANTOS",
         "FRANCISCA ALVES DE SOUZA", "ANTÔNIO MARCOS OLIVEIRA", "LUCIANA RODRIGUES LIMA", "PAULO HENRIQUE COSTA",
         "SEBASTIÃO RIBEIRO FILHO", "CONCEIÇÃO APARECIDA GOMES"]
RUAS = ["RUA DAS FLORES", "AV AFONSO PENA", "RUA DOS GUAJAJARAS", "RUA PADRE EUSTÁQUIO", "AV DO CONTORNO",
        "RUA ESPÍRITO SANTO", "RUA PERNAMBUCO", "AV AMAZONAS", "RUA DA BAHIA", "RUA TUPIS"]
BAIRROS = ["CENTRO", "SAVASSI", "FUNCIONÁRIOS", "LOURDES", "SANTA EFIGÊNIA", "PAMPULHA", "BURITIS", "PRADO",
           "CAIÇARA", "SION"]


def conta(i):
    im = Image.new("RGB", (1240 * E, 1754 * E), (252, 252, 250))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 1240 * E, 150 * E], fill=(0, 84, 147))
    d.text((60 * E, 45 * E), "COMPANHIA DE ENERGIA FICTICIA", font=F("arialbd.ttf", 44), fill="white")
    d.text((60 * E, 105 * E), "CNPJ 00.000.000/0001-91", font=F("arial.ttf", 22), fill=(220, 230, 240))
    centavos = (150 + i) * 100 + 37
    cod = codigos.arrecadacao(centavos, segmento="3", empresa="0048", livre=f"{30000000 + i:08d}".ljust(25, "0"))
    cep = 30000 + (i * 1973) % 2000
    y = 210
    for rot, val in [("NOME", NOMES[i % len(NOMES)]), ("CPF", cpf(i)),
                     ("ENDEREÇO", f"{RUAS[i % len(RUAS)]}, {(37 * i) % 1900 + 12} APTO {100 * (1 + i % 9) + 1 + i % 4}"),
                     ("BAIRRO", BAIRROS[(3 * i) % len(BAIRROS)]),
                     ("CEP", f"{cep:05d}-{(i * 211) % 900:03d}"), ("CIDADE/UF", "BELO HORIZONTE/MG"),
                     ("Nº INSTALAÇÃO", f"30{i:06d}"), ("MÊS REFERÊNCIA", "SET/2026"),
                     ("VENCIMENTO", "10/10/2026"), ("CONSUMO", f"{180 + i} kWh")]:
        d.text((60 * E, y * E), rot, font=F("arialbd.ttf", 22), fill=(90, 90, 90))
        d.text((330 * E, y * E), val, font=F("arial.ttf", 26), fill=(20, 20, 20))
        y += 52
    d.rectangle([60 * E, (y + 20) * E, 1180 * E, (y + 140) * E], outline=(0, 84, 147), width=3 * E)
    d.text((90 * E, (y + 50) * E), "TOTAL A PAGAR", font=F("arialbd.ttf", 30), fill=(0, 84, 147))
    d.text((800 * E, (y + 45) * E), f"R$ {centavos // 100},{centavos % 100:02d}", font=F("arialbd.ttf", 44),
           fill=(10, 10, 10))
    # linha digitável e barras ITF do MESMO código, que traz o total impresso
    d.text((60 * E, 1500 * E), codigos.linha_arrecadacao(cod), font=F("arial.ttf", 26), fill=(20, 20, 20))
    barras = np.repeat(codigos.perfil_barras(cod, 1080 * E)[None, :], 100 * E, axis=0)
    a = np.asarray(im).copy()
    a[1560 * E:1560 * E + barras.shape[0], 60 * E:60 * E + barras.shape[1]][barras > 0.5] = (0, 0, 0)
    im = Image.fromarray(a)
    if REALISTA:  # aparência de scan: desfoque leve + grão do papel
        im = im.filter(ImageFilter.GaussianBlur(0.7))
        a = np.asarray(im).astype(np.float32)
        a += np.random.default_rng(i).normal(0, 4.0, a.shape[:2] + (1,))
        im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    return im


def salvar(im, caminho, foto=False):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    if REALISTA:
        caminho = caminho.with_suffix(".jpg")
        if foto:  # celular: pixels deitados + EXIF dizendo para girar 90° (orientação 6)
            exif = Image.Exif()
            exif[0x0112] = 6
            im.transpose(Image.Transpose.ROTATE_90).save(caminho, quality=85, exif=exif)
            return
        im.save(caminho, quality=85)
    else:
        im.save(caminho, quality=88) if caminho.suffix == ".jpg" else im.save(caminho)


n = 0
for sp, qtd in (("treino", 4), ("teste", 2), ("validacao", 2)):
    for j in range(qtd):
        n += 1
        ext = ".jpg" if sp == "treino" and j == 1 else ".png"
        salvar(conta(n), raiz / sp / f"conta_{n:02d}{ext}", foto=REALISTA and sp == "treino" and j == 3)
pdf = [conta(50), conta(51)]
pdf[0].save(raiz / "teste" / "fatura_2pag.pdf", save_all=True, append_images=pdf[1:], resolution=150 * E)
print("ok", raiz)
