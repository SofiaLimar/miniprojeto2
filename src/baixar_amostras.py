"""
Baixa UMA vez cada página que a Etapa 1 precisa e salva o HTML em
data/raw/html/, para desenvolvermos o parse em cima dos arquivos salvos
(regra do README: "Trabalhe no arquivo local").

Rodar com: uv run python src/baixar_amostras.py
"""

import time

from scrape import DIR_RAW, HEADERS, PAUSA, URL_BASE, URL_ONDAS, URL_VENTO, baixar_html

import requests

PAGINAS = {
    "mares.html": URL_BASE,
    "ondas.html": URL_ONDAS,
    "vento.html": URL_VENTO,
}


def main() -> None:
    pasta = DIR_RAW / "html"
    pasta.mkdir(parents=True, exist_ok=True)

    # robots.txt: as regras do site sobre acesso automatizado.
    robots = requests.get("https://tabuademares.com/robots.txt", headers=HEADERS, timeout=30)
    (pasta / "robots.txt").write_text(robots.text, encoding="utf-8")
    print(f"  {robots.status_code} robots.txt")
    time.sleep(PAUSA)

    for nome, url in PAGINAS.items():
        html = baixar_html(url)
        (pasta / nome).write_text(html, encoding="utf-8")
        time.sleep(PAUSA)

    print(f"\nPronto! Arquivos salvos em: {pasta}")


if __name__ == "__main__":
    main()
