#!/usr/bin/env python3
"""Testes das regras, sem rede: python3 scripts/test_atualizar.py"""
import json
import sys
import unittest
from datetime import date, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atualizar as a  # noqa: E402

HOJE = date(2026, 11, 3)


class Categorias(unittest.TestCase):
    def test_etiquetas(self):
        casos = {
            "thrash metal": "Thrash", "Death-Doom Metal": "Doom/Sludge", "deathcore": "Nu/Metalcore",
            "melodic death metal": "Death", "blackened death metal": "Death",
            "black metal": "Black", "atmospheric sludge metal": "Doom/Sludge",
            "nu metal": "Nu/Metalcore", "nu-metal": "Nu/Metalcore", "metalcore": "Nu/Metalcore",
            "heavy metal": None, "black music": None, "numetal band": None, "pop": None,
        }
        for etiqueta, esperado in casos.items():
            self.assertEqual(a.categoria_da_etiqueta(etiqueta), esperado, etiqueta)

    def test_votos_decidem(self):
        self.assertEqual(a.categoria_por_regras({"thrash metal": 10, "death metal": 2}, []), "Thrash")
        self.assertEqual(a.categoria_por_regras({"death metal": 2}, ["thrash metal"]), "Death")
        self.assertEqual(a.categoria_por_regras({}, ["doom metal"]), "Doom/Sludge")

    def test_sem_correspondencia_fica_em_outros(self):
        self.assertEqual(a.categoria_por_regras({"heavy metal": 5}, ["rock"]), "Outros")

    def test_nao_musica(self):
        self.assertTrue(a.nao_e_musica(["Audiobook"]))
        self.assertTrue(a.nao_e_musica(["spoken-word"]))
        self.assertFalse(a.nao_e_musica(["thrash metal"]))


class Lancamentos(unittest.TestCase):
    def test_filtra_e_ordena(self):
        grupos = [
            {"id": "1", "title": "Velho", "primary-type": "Album", "first-release-date": "2001-05-02"},
            {"id": "2", "title": "Novo", "primary-type": "EP", "first-release-date": "2026-10-01"},
            {"id": "3", "title": "Best Of", "primary-type": "Album", "secondary-types": ["Compilation"], "first-release-date": "2026-10-05"},
            {"id": "4", "title": "Sem data", "primary-type": "Album"},
            {"id": "5", "title": "Ao vivo", "primary-type": "Album", "secondary-types": ["Live"], "first-release-date": "2026-09"},
            {"id": "6", "title": "Parcial", "primary-type": "Album", "first-release-date": "2026-09"},
        ]
        r = a.converter_lancamentos("Banda", grupos)
        self.assertEqual([l["titulo"] for l in r], ["Novo", "Parcial", "Velho"])
        self.assertEqual([l["tipo"] for l in r], ["EP", "Álbum", "Álbum"])

    def test_recente(self):
        self.assertTrue(a.e_recente("2026-09-01", HOJE))
        self.assertTrue(a.e_recente("2026-10", HOJE))
        self.assertFalse(a.e_recente("2026", HOJE))
        self.assertFalse(a.e_recente("2026-07-01", HOJE))
        self.assertFalse(a.e_recente("2026-12-01", HOJE))

    def test_so_audiolivros(self):
        self.assertTrue(a.so_nao_musica([{"secondary-types": ["Audiobook"]}]))
        self.assertFalse(a.so_nao_musica([{"secondary-types": ["Audiobook"]}, {"secondary-types": []}]))
        self.assertFalse(a.so_nao_musica([]))


class Concertos(unittest.TestCase):
    def test_so_portugal_e_futuros(self):
        def ev(i, pais, quando, ofertas=None):
            return {"id": i, "datetime": quando, "url": "https://b/%s" % i, "offers": ofertas or [],
                    "venue": {"country": pais, "city": "C", "name": "S"}}
        r = a.filtrar_concertos([
            ev(1, "Spain", "2026-12-01T20:00:00"),
            ev(2, "Portugal", "2026-12-20T20:00:00"),
            ev(3, "Portugal", "2026-11-10T20:00:00", [{"url": "https://bilhetes/3"}]),
            ev(4, "Portugal", "2026-01-01T20:00:00"),
            ev(5, "PT", "2026-11-15T20:00:00"),
        ], HOJE)
        self.assertEqual([c["id"] for c in r], ["3", "5", "2"])
        self.assertEqual(r[0]["bilhetes"], "https://bilhetes/3")
        self.assertTrue(r[0]["tem_bilhetes"])
        self.assertEqual(r[1]["bilhetes"], "https://b/5")

    def test_resposta_de_erro_nao_rebenta(self):
        self.assertEqual(a.filtrar_concertos({"errorMessage": "x"}, HOJE), [])

    def test_codificacao_do_nome(self):
        self.assertEqual(a.nome_para_bandsintown("AC/DC"), "AC%252FDC")
        self.assertEqual(a.nome_para_bandsintown("Mötley"), "M%C3%B6tley")


class Feeds(unittest.TestCase):
    def test_rss(self):
        xml = b"""<rss><channel><title>Canal</title>
        <item><title><![CDATA[Novo &amp; <b>bom</b>]]></title><link>https://x/1</link>
          <pubDate>Mon, 02 Nov 2026 21:47:52 +0000</pubDate></item>
        <item><title>Sem data</title><link>https://x/2</link></item>
        <item><title>Script</title><link>javascript:alert(1)</link><pubDate>Mon, 02 Nov 2026 21:47:52 +0000</pubDate></item>
        </channel></rss>"""
        r = a.analisar_feed(xml, "F")
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["titulo"], "Novo & bom")
        self.assertEqual(r[0]["data"].tzinfo, timezone.utc)

    def test_atom(self):
        xml = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title>
        <link rel="self" href="https://x/self"/><link href="https://x/a"/>
        <updated>2026-11-02T10:00:00Z</updated></entry></feed>"""
        self.assertEqual(a.analisar_feed(xml, "F")[0]["link"], "https://x/a")

    def test_rejeita_entidades(self):
        with self.assertRaises(ValueError):
            a.analisar_feed(b'<!DOCTYPE x [<!ENTITY a "b">]><rss></rss>', "F")


class Spotify(unittest.TestCase):
    def test_pagina_seguindo_next(self):
        paginas = [
            {"artists": {"items": [{"id": "1", "name": "A", "external_urls": {"spotify": "https://s/1"}}],
                         "next": "https://api.spotify.com/v1/me/following?after=1"}},
            {"artists": {"items": [{"id": "2", "name": "B", "genres": ["doom metal"]}], "next": None}},
        ]
        s = a.Spotify("i", "s", "r")
        s.acesso = "t"
        with mock.patch.object(a, "pedir", side_effect=[(200, json.dumps(p).encode()) for p in paginas]) as pedir:
            r = s.artistas_seguidos()
        self.assertEqual([x["nome"] for x in r], ["A", "B"])
        self.assertEqual(r[1]["generos_spotify"], ["doom metal"])
        self.assertIn("/me/following?type=artist&limit=50", pedir.call_args_list[0][0][0])

    def test_falha_usa_bands_txt(self):
        ambiente = {"SPOTIFY_CLIENT_ID": "i", "SPOTIFY_CLIENT_SECRET": "s", "SPOTIFY_REFRESH_TOKEN": "r"}
        avisos = []
        with mock.patch.dict(a.os.environ, ambiente), \
                mock.patch.object(a, "pedir", return_value=(400, b"{}")):
            bandas, origem, spotify = a.obter_bandas(avisos)
        self.assertEqual(origem, "bands.txt")
        self.assertIsNone(spotify)
        self.assertTrue(bandas)
        self.assertIn("bands.txt", avisos[0])

    def test_junta_link_e_capa(self):
        s = mock.Mock()
        s.albuns.return_value = iter([{"name": "Título!", "images": [{"url": "g"}, {"url": "m"}],
                                       "external_urls": {"spotify": "https://s/a"}}])
        banda = {"spotify_id": "x", "lancamentos": [
            {"titulo": "titulo", "data": "2026-10-01", "capa": "c", "spotify": "p"}]}
        a.juntar_spotify(s, banda, HOJE)
        self.assertEqual(banda["lancamentos"][0]["capa"], "m")
        self.assertEqual(banda["lancamentos"][0]["spotify"], "https://s/a")


if __name__ == "__main__":
    unittest.main()
