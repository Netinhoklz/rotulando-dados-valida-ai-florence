import random
import unittest

import cv2
import numpy as np

from rotulador import canal
from tests.util import conta


def par():
    """Uma conta e a mesma conta com um retângulo de texto trocado (a "edição")."""
    a = cv2.resize(conta(7), (640, 840), interpolation=cv2.INTER_CUBIC)
    b = a.copy()
    b[300:330, 100:260] = (40, 40, 40)
    regiao = np.zeros(a.shape[:2], bool)
    regiao[300:330, 100:260] = True
    return a, b, regiao


class TestCanal(unittest.TestCase):
    def test_par_igual_fora_da_edicao_em_todos_os_perfis(self):
        """O canal é o mesmo nas duas imagens: fora da vizinhança da edição nada difere (senão o
        canal vira atalho entre as classes)."""
        a, b, regiao = par()
        rng = random.Random(1)
        for perfil in canal.PERFIS:
            for _ in range(6):
                th = canal.sortear(rng, {"perfil": perfil})
                (oa, ob), (reg,), _ = canal.aplicar([a, b], [regiao], [], th)
                self.assertIsNone(canal.conferir_par(oa, ob, reg, th), (perfil, canal.descrever(th)))
                # sem os codecs, nada difere fora do raio: todo campo (ruído, luz, sombra) é do par
                sem = {**th, "passos": [{k: v for k, v in p.items() if k not in ("jpeg", "webp", "tabela")}
                                        for p in th["passos"]]}
                (oa, ob), (reg,), _ = canal.aplicar([a, b], [regiao], [], sem)
                r = canal.raio(sem)
                perto = cv2.dilate(reg.astype(np.uint8), np.ones((2 * r + 1, 2 * r + 1), np.uint8)) > 0
                self.assertEqual(int(np.abs(oa.astype(int) - ob.astype(int)).max(2)[~perto].max(initial=0)), 0,
                                 (perfil, canal.descrever(th)))

    def test_mascara_acompanha_a_edicao(self):
        a, b, regiao = par()
        rng = random.Random(2)
        for perfil in canal.PERFIS:
            th = canal.sortear(rng, {"perfil": perfil})
            (oa, ob), (reg,), _ = canal.aplicar([a, b], [regiao], [], th)
            forte = np.abs(oa.astype(int) - ob.astype(int)).max(2) > 40
            perto = cv2.dilate(reg.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
            self.assertGreater(reg.sum(), 0, perfil)
            self.assertGreater((forte & perto).sum() / max(1, forte.sum()), 0.97, (perfil, canal.descrever(th)))

    def test_deterministico_e_rotulos(self):
        a, b, regiao = par()
        rot = regiao.astype(np.uint16) * 7
        th = canal.sortear(random.Random(3), {"perfil": "celular"})
        s1 = canal.aplicar([a, b], [regiao], [rot], th)
        s2 = canal.aplicar([a, b], [regiao], [rot], th)
        self.assertTrue(all(np.array_equal(x, y) for x, y in zip(s1[0], s2[0])))
        reg, r = s1[1][0], canal.ajustar_rotulos(s1[2][0], s1[1][0])
        self.assertTrue(((r > 0) == reg).all() and set(np.unique(r)) <= {0, 7})

    def test_traco_fino_continua_rotulado(self):
        """Edição de 1–2 px (um traço) reduzida pelo canal: a imagem ainda mostra a mudança, então a
        máscara e os rótulos não podem perdê-la (vizinho mais próximo pulava o traço inteiro)."""
        a = cv2.resize(conta(7), (900, 1200), interpolation=cv2.INTER_CUBIC)
        b, reg, rot = a.copy(), np.zeros(a.shape[:2], bool), np.zeros(a.shape[:2], np.uint16)
        for i in range(10):
            y, x = 200 + 80 * i, 150 + 30 * i
            larg = 1 + i % 2
            b[y:y + larg, x:x + 300] = 20
            reg[y:y + larg, x:x + 300] = True
            rot[y:y + larg, x:x + 300] = 3 + i % 2
        for passos in ([{"tipo": "scanner", "angulo": 0.7, "escala": 0.55, "desfoque": 0.3, "gama": 1.0,
                         "contraste": 1.0, "brilho": 0.0, "ruido": 0.0, "jpeg": None}],
                       [{"tipo": "celular", "escala": 0.55, "margem": 0.03, "cantos": [0.01, -0.02, 0.0, 0.02, -0.01, 0.0, 0.02, 0.01],
                         "fundo": [90, 90, 90], "luz": 0.1, "luz_angulo": 30.0, "vinheta": 0.1, "sombra": None,
                         "balanco": [1.0, 1.0, 1.0], "desfoque": 0.4, "movimento": None, "ruido_a": 0.0, "ruido_b": 0.0,
                         "nitidez": 0.3}]):
            th = {"perfil": "teste", "semente": 1, "passos": passos}
            (oa, ob), (r,), (rr,) = canal.aplicar([a, b], [reg], [rot], th)
            rr = canal.ajustar_rotulos(rr, r)
            self.assertGreater((rr > 0).sum() / max(1, r.sum()), 0.98, passos[0]["tipo"])  # rótulo cobre a região
            forte = np.abs(oa.astype(int) - ob.astype(int)).max(2) > 40
            perto = cv2.dilate(r.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
            self.assertLess(int((forte & ~perto).sum()), 5, passos[0]["tipo"])  # mudança forte sem rótulo
            self.assertTrue(set(np.unique(rr)) <= {0, 3, 4})

    def test_imagem_pequena_com_cortes_nao_quebra(self):
        a = np.full((40, 60, 3), 230, np.uint8)
        b = a.copy()
        b[10:20, 10:30] = 30
        reg = np.zeros((40, 60), bool)
        reg[10:20, 10:30] = True
        th = {"perfil": "t", "semente": 2, "passos": [{"tipo": "celular", **canal.sortear(random.Random(5), {"perfil": "celular"})["passos"][0], "escala": 0.45}]
              + [{"tipo": "whatsapp", "lado": 1600, "jpeg": 75, "deslocamento": [7, 7]}] * 3}
        (oa, ob), (r,), _ = canal.aplicar([a, b], [reg], [], th)
        self.assertEqual(oa.shape, ob.shape)
        self.assertGreater(min(oa.shape[:2]), 0)

    def test_fracao_e_validacao(self):
        self.assertIsNone(canal.sortear(random.Random(0), {"perfil": "misturado", "prob": 0}))
        self.assertIsNone(canal.sortear(random.Random(0), {"perfil": "nenhum"}))
        self.assertIsNone(canal.normalizar(None))
        with self.assertRaises(ValueError):
            canal.normalizar({"perfil": "fax"})
        th = canal.sortear(random.Random(0), {"perfil": "whatsapp"})
        self.assertTrue(any(p["tipo"] == "whatsapp" for p in th["passos"]))
        self.assertIn("WhatsApp".lower(), canal.descrever(th).lower())

    def test_fases_do_sorteio(self):
        """Scanner, celular e app sem impressão vêm antes da edição (e o recorte parcial feito nessa captura
        também); impressão (e a captura que a segue), tela, captura e transmissões vêm depois. captura="depois"
        é o comportamento antigo."""
        rng = random.Random(4)
        vistos = set()
        for _ in range(200):
            th = canal.sortear(rng, {"perfil": "misturado"})
            tipos = [p["tipo"] for p in th["passos"]]
            vistos.update(tipos)
            fisica_antes = any(t in ("scanner", "celular", "app_scanner") for t in tipos) and "impressao" not in tipos
            for p in th["passos"]:
                esperada = "antes" if p["tipo"] in ("scanner", "celular", "app_scanner", "recorte") and fisica_antes \
                    else "depois"
                self.assertEqual(p["fase"], esperada, canal.descrever(th))
            fases = [p["fase"] for p in th["passos"]]
            self.assertEqual(fases, sorted(fases), fases)  # os de antes vêm primeiro
        self.assertTrue({"app_scanner", "whatsapp_documento", "impressao", "tela"} <= vistos, vistos)
        self.assertTrue(all(p["fase"] == "depois" for _ in range(30)
                            for p in canal.sortear(rng, {"perfil": "celular"}, captura="depois")["passos"]))
        self.assertEqual(canal.normalizar({"perfil": "misturado", "captura": "depois"})["captura"], "depois")
        self.assertEqual(canal.normalizar("scanner")["captura"], "auto")
        with self.assertRaises(ValueError):
            canal.normalizar({"perfil": "scanner", "captura": "durante"})
        self.assertEqual(canal.sortear(rng, {"perfil": "scanner", "captura": "depois"})["passos"][0]["fase"], "depois")
        # entrada já capturada (foto/scan em JPEG): metade das vezes a captura de antes some; sobra
        # transmissão ou nada, e aplicar/descrever continuam funcionando
        sem = 0
        for _ in range(80):
            th = canal.sortear(rng, {"perfil": "scanner"}, entrada_capturada=True)
            if not canal.captura_fisica(th):
                sem += 1
                self.assertTrue(all(p["fase"] == "depois" for p in th["passos"]))
                self.assertTrue(canal.descrever(th))
        self.assertTrue(15 < sem < 65, sem)
        self.assertTrue(all(canal.captura_fisica(canal.sortear(rng, {"perfil": "scanner"}, captura="depois",
                                                               entrada_capturada=True)) for _ in range(20)))

    def test_aplicar_por_fase_e_theta_antigo(self):
        a, b, regiao = par()
        scanner = {"tipo": "scanner", "angulo": 0.0, "escala": 0.5, "desfoque": 0.3, "gama": 1.0, "contraste": 1.0,
                   "brilho": 0.0, "ruido": 0.0, "jpeg": None, "fase": "antes"}
        zap = {"tipo": "whatsapp", "lado": 1600, "jpeg": 80, "deslocamento": [4, 6], "fase": "depois"}
        th = {"perfil": "t", "semente": 1, "passos": [scanner, zap]}
        (oa,), (reg,), _ = canal.aplicar([a], [regiao], [], th, fase="antes")
        self.assertEqual(oa.shape[:2], (420, 320))  # só o scanner (metade)
        (od,), (regd,), _ = canal.aplicar([oa], [reg], [], th, fase="depois")
        self.assertEqual(od.shape[:2], (414, 316))  # só o corte do WhatsApp
        (tudo,), (regt,), _ = canal.aplicar([a], [regiao], [], th)
        self.assertEqual(tudo.shape, od.shape)
        self.assertTrue(np.array_equal(regt, regd))
        self.assertLess(np.abs(tudo.astype(int) - od.astype(int)).mean(), 1.5)  # mesmos campos; só o arredondamento
        # theta antigo, sem "fase": tudo conta como "depois"
        velho = {**th, "passos": [{k: v for k, v in p.items() if k != "fase"} for p in th["passos"]]}
        (sa,), (sr,), _ = canal.aplicar([a], [regiao], [], velho, fase="antes")
        self.assertTrue(np.array_equal(sa, a) and np.array_equal(sr, regiao))
        self.assertEqual(canal.aplicar([a], [regiao], [], velho, fase="depois")[0][0].shape, tudo.shape)
        self.assertEqual(canal.aplicar([a], [regiao], [], velho)[0][0].shape, tudo.shape)
        self.assertEqual(canal.raio(th, "antes"), canal.raio({**th, "passos": [scanner]}))
        self.assertTrue(canal.captura_fisica(th, "antes") and not canal.captura_fisica(th, "depois"))
        self.assertTrue(canal.captura_fisica(velho, "depois") and not canal.captura_fisica(velho, "antes"))
        self.assertFalse(canal.tem_webp(th, "depois"))

    def test_pontos_acompanham_a_geometria(self):
        """Pontos da entrada caem onde a máscara correspondente foi parar: giro e escala, perspectiva,
        recorte, curvatura, lente e a retificação do app."""
        a, _, _ = par()
        pts = [(120, 200), (500, 700), (320, 420)]
        masks = []
        for x, y in pts:
            m = np.zeros(a.shape[:2], bool)
            m[y - 4:y + 5, x - 4:x + 5] = True
            masks.append(m)
        foto = canal.sortear(random.Random(5), {"perfil": "celular"})["passos"][0]
        curva = {"ondas": [["y", 0.02, 1.0, 0.25], ["x", 0.012, 1.5, 0.6]], "vinco": ["y", 0.5, 0.02]}
        lente = {"k1": 0.12, "cromatica": 1.5}
        casos = {
            "scanner": ([{"tipo": "scanner", "angulo": 1.2, "escala": 0.6, "desfoque": 0.3, "gama": 1.0,
                          "contraste": 1.0, "brilho": 0.0, "ruido": 0.0, "jpeg": None}], 2),
            "celular": ([{**foto, "escala": 0.7, "curvatura": None, "lente": None, "jpeg": None}], 2),
            "transmissao": ([{"tipo": "whatsapp", "lado": 500, "jpeg": 80, "deslocamento": [5, 3]}], 2),
            "curvatura e lente": ([{**foto, "escala": 0.7, "curvatura": curva, "lente": lente, "jpeg": None}], 3),
            "app_scanner": ([{**canal.sortear(random.Random(9), {"perfil": "app_scanner"})["passos"][0],
                              "curvatura": curva, "lente": lente, "jpeg": None}], 3),
            "tela": ([{**canal.sortear(random.Random(9), {"perfil": "tela"})["passos"][0], "jpeg": None,
                       "lente": {"k1": -0.1, "cromatica": 1.0}}], 3),
        }
        for nome, (passos, tolerancia) in casos.items():
            th = {"perfil": "t", "semente": 1, "passos": passos}
            saida = canal.aplicar([a, a], masks, [], th, pontos=pts)
            self.assertEqual(len(saida), 4)
            for m, (px, py) in zip(saida[1], saida[3]):
                ys, xs = np.nonzero(m)
                self.assertTrue(ys.size, nome)
                self.assertLessEqual(float(np.hypot(xs.mean() - px, ys.mean() - py)), tolerancia, nome)
            self.assertEqual(len(canal.aplicar([a], masks, [], th)), 3)  # sem pontos, a 3-tupla de sempre

    def test_app_scanner_nos_tres_modos(self):
        """O app recorta/retifica e realça: o par só difere perto da edição; pb deixa o papel quase branco
        e a tinta quase preta; cinza e pb saem sem cor."""
        a, b, regiao = par()
        base = canal.sortear(random.Random(11), {"perfil": "app_scanner"})["passos"][0]
        base.update({"sombra": None, "movimento": None})
        for modo, extra in (("cor", {"preto": 30.0, "realce": 1.2, "saturacao": 0.2}),
                            ("cinza", {"preto": 30.0, "realce": 0.8}), ("pb", {"limiar": 0.2, "suavidade": 7.0})):
            p = {k: v for k, v in base.items() if k not in ("preto", "realce", "saturacao", "limiar", "suavidade")}
            th = {"perfil": "app_scanner", "semente": 7, "passos": [{**p, "modo": modo, **extra}]}
            (oa, ob), (reg,), _ = canal.aplicar([a, b], [regiao], [], th)
            self.assertIsNone(canal.conferir_par(oa, ob, reg, th), modo)
            self.assertTrue(reg.any() and oa.shape == ob.shape, modo)
            forte = np.abs(oa.astype(int) - ob.astype(int)).max(2) > 40
            perto = cv2.dilate(reg.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
            self.assertGreater((forte & perto).sum() / max(1, forte.sum()), 0.97, modo)
            L = oa @ np.float32([0.299, 0.587, 0.114])
            if modo == "pb":
                self.assertGreaterEqual(float(np.median(L[L > np.percentile(L, 50)])), 240)  # papel
                self.assertLess(float(np.percentile(L, 3)), 40)  # tinta (o cabeçalho escuro)
            if modo != "cor":
                self.assertLessEqual(int(np.abs(oa[..., 0].astype(int) - oa[..., 2].astype(int)).max()), 2)
            self.assertIn(f"modo {modo}", canal.descrever(th))
        self.assertTrue(canal.captura_fisica(th))

    def test_curvatura_e_lente_levam_as_mascaras(self):
        """Traços finos com papel curvado (ondas e vinco) e lente (barril, aberração cromática), no celular
        e no app: o rótulo cobre a região e nenhuma mudança forte fica sem rótulo (como na geometria)."""
        a = cv2.resize(conta(7), (900, 1200), interpolation=cv2.INTER_CUBIC)
        b, reg, rot = a.copy(), np.zeros(a.shape[:2], bool), np.zeros(a.shape[:2], np.uint16)
        for i in range(10):
            y, x = 200 + 80 * i, 150 + 30 * i
            larg = 1 + i % 2
            b[y:y + larg, x:x + 300] = 20
            reg[y:y + larg, x:x + 300] = True
            rot[y:y + larg, x:x + 300] = 3 + i % 2
        curva = {"ondas": [["y", 0.02, 1.0, 0.25], ["x", 0.012, 1.5, 0.6]], "vinco": ["x", 0.4, -0.02]}
        lente = {"k1": -0.12, "cromatica": 1.5}
        celular = {**canal.sortear(random.Random(5), {"perfil": "celular"})["passos"][0], "escala": 0.6, "sombra": None,
                   "movimento": None, "ruido_a": 0.0, "ruido_b": 0.0, "jpeg": None, "curvatura": curva, "lente": lente}
        app = {**canal.sortear(random.Random(9), {"perfil": "app_scanner"})["passos"][0], "escala": 0.6, "sombra": None,
               "movimento": None, "ruido_a": 0.0, "ruido_b": 0.0, "jpeg": None, "curvatura": curva, "lente": lente,
               "modo": "cor", "preto": 30.0, "realce": 1.2, "saturacao": 0.2}
        for passos, k in (([celular], 3), ([app], 7)):  # a nitidez forte do app faz um halo de ~3 px
            th = {"perfil": "teste", "semente": 1, "passos": passos}
            (oa, ob), (r,), (rr,) = canal.aplicar([a, b], [reg], [rot], th)
            rr = canal.ajustar_rotulos(rr, r)
            self.assertGreater((rr > 0).sum() / max(1, r.sum()), 0.98, passos[0]["tipo"])
            forte = np.abs(oa.astype(int) - ob.astype(int)).max(2) > 40
            perto = cv2.dilate(r.astype(np.uint8), np.ones((k, k), np.uint8)) > 0
            self.assertLess(int((forte & ~perto).sum()), 5, passos[0]["tipo"])
            self.assertTrue(set(np.unique(rr)) <= {0, 3, 4})
            self.assertIn("papel curvado, lente", canal.descrever(th))

    def test_reticula_so_no_meio_tom(self):
        """Papel e tinta preta ficam exatamente como estão; o meio-tom ganha o padrão de pontos."""
        a = np.full((160, 200, 3), 246, np.uint8)
        a[20:60, 20:180] = 8
        a[80:140, 20:180] = (120, 140, 160)
        p = {"tipo": "impressao", "espalhamento": 0.0, "toner": 0.08, "papel": 2.0}
        com = {"perfil": "t", "semente": 2, "passos": [{**p, "reticula": {"periodo": 4.0, "angulos": [15, 45, 75],
                                                                           "forca": 0.9}}]}
        (oc,), _, _ = canal.aplicar([a], [], [], com)
        (os_,), _, _ = canal.aplicar([a], [], [], {**com, "passos": [p]})
        d = np.abs(oc.astype(int) - os_.astype(int)).max(2)
        papel = np.ones(a.shape[:2], bool)
        papel[20:140, 20:180] = False
        self.assertEqual(int(d[papel].max()), 0)
        self.assertEqual(int(d[20:60, 20:180].max()), 0)
        self.assertGreater(float(d[80:140, 20:180].mean()), 20)  # o padrão
        self.assertIn("retícula", canal.descrever(com))
        self.assertNotIn("retícula", canal.descrever({**com, "passos": [p]}))

    def test_transmissao_como_documento_e_descricoes(self):
        a, b, regiao = par()
        th = {"perfil": "t", "semente": 1, "passos": [{"tipo": "whatsapp_documento", "fase": "depois"}]}
        (oa,), (reg,), _ = canal.aplicar([a], [regiao], [], th)
        self.assertTrue(np.array_equal(oa, a) and np.array_equal(reg, regiao))
        self.assertFalse(canal.captura_fisica(th))
        self.assertIn("documento", canal.descrever(th))
        self.assertAlmostEqual(sum(canal.TRANSMISSOES.values()), 1.0)
        rng = random.Random(8)
        self.assertIn("(antes da edição)", canal.descrever(canal.sortear(rng, {"perfil": "celular"})))
        self.assertNotIn("antes da edição", canal.descrever(canal.sortear(rng, {"perfil": "celular"}, captura="depois")))
        self.assertIn("app de scanner", canal.descrever(canal.sortear(rng, {"perfil": "app_scanner"})))
        self.assertIn("Aplicativo de scanner", canal.NOMES["app_scanner"])
        self.assertTrue(canal.descrever({"perfil": "scanner", "semente": 1, "passos": []}))


if __name__ == "__main__":
    unittest.main()
