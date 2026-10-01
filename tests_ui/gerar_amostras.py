"""Gera contas de luz sintéticas (A4, 150 dpi) para testar a interface."""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

raiz = Path(sys.argv[1])
F = lambda n, s: ImageFont.truetype(f"C:/Windows/Fonts/{n}", s)

def conta(i):
    im = Image.new("RGB", (1240, 1754), (252, 252, 250))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 1240, 150], fill=(0, 84, 147))
    d.text((60, 45), "COMPANHIA DE ENERGIA FICTICIA", font=F("arialbd.ttf", 44), fill="white")
    d.text((60, 105), "CNPJ 00.000.000/0001-91", font=F("arial.ttf", 22), fill=(220, 230, 240))
    y = 210
    for rot, val in [("NOME", f"MARIA DOS SANTOS {i:02d}"), ("CPF", "123.456.789-09"),
                     ("ENDEREÇO", f"RUA DAS FLORES, {100 + i} APTO 12"), ("BAIRRO", "CENTRO"),
                     ("CEP", "30123-456"), ("CIDADE/UF", "BELO HORIZONTE/MG"),
                     ("Nº INSTALAÇÃO", f"30{i:06d}"), ("MÊS REFERÊNCIA", "SET/2026"),
                     ("VENCIMENTO", "10/10/2026"), ("CONSUMO", f"{180 + i} kWh")]:
        d.text((60, y), rot, font=F("arialbd.ttf", 22), fill=(90, 90, 90))
        d.text((330, y), val, font=F("arial.ttf", 26), fill=(20, 20, 20))
        y += 52
    d.rectangle([60, y + 20, 1180, y + 140], outline=(0, 84, 147), width=3)
    d.text((90, y + 50), "TOTAL A PAGAR", font=F("arialbd.ttf", 30), fill=(0, 84, 147))
    d.text((800, y + 45), f"R$ {150 + i},37", font=F("arialbd.ttf", 44), fill=(10, 10, 10))
    d.text((60, 1500), "83660000001-2 50370048000-6 12345678901-2 34567890123-4", font=F("arial.ttf", 26), fill=(20, 20, 20))
    for k in range(120):  # barras falsas
        x = 60 + k * 9
        d.rectangle([x, 1560, x + (2 if k % 3 else 5), 1660], fill=(0, 0, 0))
    return im

n = 0
for sp, qtd in (("treino", 4), ("teste", 2), ("validacao", 2)):
    for j in range(qtd):
        n += 1
        im = conta(n)
        (raiz / sp).mkdir(parents=True, exist_ok=True)
        if sp == "treino" and j == 1:
            im.save(raiz / sp / f"conta_{n:02d}.jpg", quality=88)
        else:
            im.save(raiz / sp / f"conta_{n:02d}.png")
pdf = [conta(50), conta(51)]
pdf[0].save(raiz / "teste" / "fatura_2pag.pdf", save_all=True, append_images=pdf[1:], resolution=150)
print("ok", raiz)
