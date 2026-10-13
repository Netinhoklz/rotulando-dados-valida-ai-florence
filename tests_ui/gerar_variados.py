"""Contas sintéticas VARIADAS para testar as técnicas automáticas fora do caso fácil (tudo em Arial).

    python tests_ui/gerar_variados.py PASTA

Cada documento muda fonte, cor da tinta, papel, resolução e disposição (rótulo ao lado, rótulo em
cima, "NOME: valor" na mesma linha) e tem código de barras ITF de verdade, QR PIX, duas datas e
dois valores. Um deles imita foto de celular (perspectiva, inclinação, luz desigual).
"""
import random
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rotulador.ops import codigos, geradores  # noqa: E402

raiz = Path(sys.argv[1])


def F(nome, tam):
    return ImageFont.truetype(f"C:/Windows/Fonts/{nome}", tam)


ESTILOS = [  # nome, split, fontes (valor, rótulo), tinta, papel, dpi, disposição, degradação
    dict(nome="calibri", split="treino", valor="calibri.ttf", rotulo="calibrib.ttf", tinta=(25, 25, 25),
         papel=(250, 250, 247), dpi=300, disp="lado", ruido=3.5, desfoque=0.6, jpeg=90),
    dict(nome="verdana_azul", split="treino", valor="verdana.ttf", rotulo="verdanab.ttf", tinta=(22, 45, 120),
         papel=(253, 250, 238), dpi=300, disp="cima", ruido=3.0, desfoque=0.8, jpeg=88),
    dict(nome="times", split="treino", valor="times.ttf", rotulo="timesbd.ttf", tinta=(15, 15, 15),
         papel=(244, 238, 222), dpi=300, disp="linha", ruido=5.0, desfoque=0.7, jpeg=72),
    dict(nome="courier", split="treino", valor="cour.ttf", rotulo="courbd.ttf", tinta=(60, 60, 60),
         papel=(236, 236, 232), dpi=200, disp="lado", ruido=6.0, desfoque=0.9, jpeg=80),
    dict(nome="tahoma_150dpi", split="teste", valor="tahomabd.ttf", rotulo="tahoma.ttf", tinta=(10, 10, 10),
         papel=(255, 255, 255), dpi=150, disp="lado", ruido=2.0, desfoque=0.4, jpeg=92),
    dict(nome="foto_celular", split="teste", valor="arial.ttf", rotulo="arialbd.ttf", tinta=(30, 30, 35),
         papel=(245, 243, 238), dpi=300, disp="lado", ruido=4.0, desfoque=1.1, jpeg=82, foto=True),
    # fontes FORA das 10 mais usadas: conta real usa DIN, fontes condensadas e de impressora; com todos os originais
    # nas fontes do editor, a "mesma fonte" casava sempre perfeitamente
    dict(nome="trebuchet", split="treino", valor="trebuc.ttf", rotulo="trebucbd.ttf", tinta=(35, 35, 40),
         papel=(248, 247, 243), dpi=300, disp="lado", ruido=3.5, desfoque=0.7, jpeg=85),
    dict(nome="bahnschrift", split="teste", valor="bahnschrift.ttf", rotulo="bahnschrift.ttf", tinta=(20, 20, 20),
         papel=(250, 250, 250), dpi=200, disp="cima", ruido=3.0, desfoque=0.6, jpeg=86),
]

# um cliente por documento, em Curitiba (CEP na faixa 80000-000 a 82999-999); alguns nomes e bairros com acento
NOMES = ["ANA PAULA FERREIRA COSTA", "JOSÉ CARLOS RIBEIRO", "LUCIANA MARTINS ALVES", "PEDRO HENRIQUE SOUZA",
         "MARCOS VINÍCIUS ARAÚJO", "ROSÂNGELA DIAS PEREIRA", "TIAGO ANDRADE LOPES", "CAMILA ROCHA NUNES",
         "SEBASTIÃO GOMES VIEIRA", "BEATRIZ CARDOSO MOURA"]
RUAS = ["AV BRASIL", "RUA XV DE NOVEMBRO", "AV SETE DE SETEMBRO", "RUA MARECHAL DEODORO", "AV REPÚBLICA ARGENTINA",
        "RUA EMILIANO PERNETA", "AV VICENTE MACHADO", "RUA ITUPAVA", "AV IGUAÇU", "RUA JOÃO NEGRÃO"]
BAIRROS = ["JARDIM AMERICA", "BATEL", "ÁGUA VERDE", "PORTÃO", "CABRAL", "BIGORRILHO", "JUVEVÊ", "MERCÊS",
           "BOQUEIRÃO", "CAJURU"]


def conta(e, i):
    s = e["dpi"] / 300  # tamanhos pensados em 300 dpi
    W, H = int(2480 * s), int(3508 * s)
    im = Image.new("RGB", (W, H), e["papel"])
    d = ImageDraw.Draw(im)
    fv, fr = F(e["valor"], int(52 * s)), F(e["rotulo"], int(40 * s))
    d.rectangle([0, 0, W, int(260 * s)], fill=(30, 90, 60) if i % 2 else (120, 30, 40))
    d.text((int(110 * s), int(70 * s)), f"SANEAMENTO REGIONAL {'NORTE' if i % 2 else 'SUL'}", font=F(e["rotulo"], int(76 * s)),
           fill="white")
    # um cliente por documento: o mesmo nome/CPF em dois splits seria vazamento (auditar_vazamento.py acusa)
    with geradores.usando(random.Random(2000 + i)):
        cpf = geradores.cpf()
    centavos = (1384 + 13 * i) * 100 + 57 + i  # o total impresso, o código de barras e o QR trazem o mesmo valor
    campos = [("NOME", NOMES[i % len(NOMES)]),
              ("CPF", cpf),
              ("ENDEREÇO", f"{RUAS[i % len(RUAS)]}, {1834 + 7 * i} APTO {38 + i}"), ("BAIRRO", BAIRROS[i % len(BAIRROS)]),
              ("CEP", f"{80010 + 287 * i:05d}-{(436 + 97 * i) % 900:03d}"), ("CIDADE/UF", "CURITIBA/PR"),
              ("MATRÍCULA", f"38{i}15634"),
              ("MÊS REFERÊNCIA", "AGO/2026"), ("VENCIMENTO", f"1{i}/09/2026"), ("DATA DA LEITURA", f"2{i}/08/2026"),
              ("CONSUMO", f"{183 + 11 * i} m³"), ("MULTA", f"R$ {3 + i},48")]
    y = int(380 * s)
    passo = int(118 * s) if e["disp"] != "cima" else int(150 * s)
    for k, (rot, val) in enumerate(campos):
        if e["disp"] == "lado":
            d.text((int(120 * s), y + int(8 * s)), rot, font=fr, fill=(95, 95, 95))
            d.text((int(720 * s), y), val, font=fv, fill=e["tinta"])
        elif e["disp"] == "cima":  # boleto: rótulo pequeno em cima, valor embaixo (duas colunas)
            col = (k % 2) * int(1150 * s)
            yy = y + (k // 2) * passo - (k % 2) * 0
            d.text((int(120 * s) + col, yy), rot, font=F(e["rotulo"], int(30 * s)), fill=(110, 110, 110))
            d.text((int(120 * s) + col, yy + int(42 * s)), val, font=fv, fill=e["tinta"])
            continue
        else:  # "NOME: valor" na mesma linha
            d.text((int(120 * s), y), f"{rot}: {val}", font=fv, fill=e["tinta"])
        y += passo
    if e["disp"] == "cima":
        y = int(380 * s) + 6 * passo + int(40 * s)
    y += int(60 * s)
    d.rectangle([int(110 * s), y, W - int(110 * s), y + int(250 * s)], outline=e["tinta"], width=max(2, int(6 * s)))
    d.text((int(160 * s), y + int(90 * s)), "TOTAL A PAGAR", font=F(e["rotulo"], int(60 * s)), fill=e["tinta"])
    d.text((int(1500 * s), y + int(80 * s)), f"R$ {centavos // 100:,}".replace(",", ".") + f",{centavos % 100:02d}",
           font=F(e["valor"], int(88 * s)), fill=e["tinta"])
    # código de barras ITF de verdade e linha digitável coerente
    cod = codigos.arrecadacao(centavos, segmento="2", empresa=f"{3820 + i}", livre=f"{i}" * 25, ref="8")
    linha = codigos.linha_arrecadacao(cod)
    yb = H - int(700 * s)
    d.text((int(120 * s), yb), linha, font=F(e["valor"], int(48 * s)), fill=e["tinta"])
    perfil = codigos.perfil_barras(cod, int(1900 * s))
    barras = np.repeat(perfil[None, :], int(200 * s), axis=0)
    a = np.asarray(im).copy()
    by, bx = yb + int(110 * s), int(120 * s)
    a[by:by + barras.shape[0], bx:bx + barras.shape[1]][barras > 0] = (0, 0, 0)
    im = Image.fromarray(a)
    # QR PIX
    import segno
    qr = segno.make(codigos.pix("12345678000195", "SANEAMENTO REGIONAL", "CURITIBA", f"{centavos / 100:.2f}", "TX" + str(i) * 8),
                    error="m")
    buf = qr.to_pil(scale=max(4, int(9 * s)), border=0) if hasattr(qr, "to_pil") else None
    if buf is None:
        import io
        b = io.BytesIO()
        qr.save(b, kind="png", scale=max(4, int(9 * s)), border=0)
        b.seek(0)
        buf = Image.open(b)
    buf = buf.convert("RGB")
    im.paste(buf, (W - int(160 * s) - buf.width, y + int(320 * s)))  # logo abaixo do total
    # aparência de scan
    im = im.filter(ImageFilter.GaussianBlur(e["desfoque"]))
    a = np.asarray(im).astype(np.float32)
    a += np.random.default_rng(i + 7).normal(0, e["ruido"], a.shape[:2] + (1,))
    if e.get("foto"):  # foto de celular: luz desigual, perspectiva leve e inclinação
        yy, xx = np.mgrid[0:a.shape[0], 0:a.shape[1]].astype(np.float32)
        luz = 0.82 + 0.18 * (xx / a.shape[1]) * (1 - 0.3 * yy / a.shape[0])
        a = a * luz[..., None]
        a = np.clip(a, 0, 255).astype(np.uint8)
        h, w = a.shape[:2]
        orig = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        dest = np.float32([[w * 0.02, h * 0.01], [w * 0.985, 0], [w, h * 0.995], [0, h * 0.985]])
        M = cv2.getPerspectiveTransform(orig, dest)
        a = cv2.warpPerspective(a, M, (w, h), borderMode=cv2.BORDER_CONSTANT, borderValue=(60, 55, 50))
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)), e["jpeg"]


for i, e in enumerate(ESTILOS):
    im, q = conta(e, i)
    destino = raiz / e["split"] / f"{e['nome']}.jpg"
    destino.parent.mkdir(parents=True, exist_ok=True)
    im.save(destino, quality=q)
    print(destino, im.size)
# validação: mais um documento (outra pessoa), para os três splits terem dados
e = dict(ESTILOS[0], nome="calibri_b", split="validacao")
im, q = conta(e, 9)
(raiz / "validacao").mkdir(parents=True, exist_ok=True)
im.save(raiz / "validacao" / "calibri_b.jpg", quality=q)
print("ok", raiz)
