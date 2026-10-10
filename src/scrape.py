"""
Etapa 1 — coleta de dados de tabuademares.com/br/paraiba/joao-pessoa.

Objetivo: gravar em data/raw/ os CSVs que as Etapas 2 e 3 vão consumir.

    mares_<ano>.csv      tábua de marés do ano (4 marés por dia)
    mares_previsao.csv   marés do mês corrente, para cruzar com a previsão
    ondas.csv            altura de onda hora a hora (~7 dias à frente)
    vento.csv            velocidade do vento hora a hora (~7 dias à frente)

Como funciona: cada página é baixada com `requests` (GET; a troca de mês da
tábua de marés é um POST com o campo "fecha"), e o BeautifulSoup localiza os
dados pelas classes do HTML, descobertas inspecionando a página no DevTools.
Os valores são convertidos (vírgula decimal, horários, datas sem ano) ainda
no parse, e cada CSV recebe uma linha por evento de maré ou por hora.

Colunas gravadas:
    marés:         data, hora, tipo (alta/baixa), altura_m, coeficiente,
                   coeficiente_nivel, idade_lua (0 = lua nova, ~15 = cheia)
    ondas / vento: data, hora, direcao, altura_onda_m / vento_kmh

O script src/baixar_amostras.py salva o HTML das páginas uma única vez, para
desenvolver e testar o parse sem acessar o site repetidamente.

Rodar com: uv run python src/scrape.py
"""

import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

URL_BASE = "https://tabuademares.com/br/paraiba/joao-pessoa"
URL_ONDAS = f"{URL_BASE}/previsao/ondas"
URL_VENTO = f"{URL_BASE}/previsao/vento"

# Servidores rejeitam clientes sem User-Agent. Identifique-se.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Segundos de pausa entre requests. Não tire isso.
PAUSA = 1.5

DIR_RAW = Path(__file__).resolve().parent.parent / "data" / "raw"

# As páginas de previsão mostram o mês abreviado ("OUT"); isto converte para número.
MESES_PT = {
    "JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12,
}


def baixar_html(url: str, dados_post: dict | None = None) -> str:
    """Baixa uma página e devolve o HTML.

    Use POST (passando `dados_post`) quando a página só devolver o conteúdo
    que você quer em resposta a um formulário; GET no resto.
    """
    # Sem dados de formulário -> GET (só "pedir a página").
    # Com dados de formulário -> POST (como quando você clica num botão de um
    # site e ele envia informações, por exemplo "quero o mês 3").
    if dados_post is None:
        resp = requests.get(url, headers=HEADERS, timeout=30)
    else:
        resp = requests.post(url, headers=HEADERS, data=dados_post, timeout=30)

    # Se o servidor responder com erro (404, 403, 500...), para aqui com uma
    # mensagem clara, em vez de seguir com um HTML de página de erro.
    resp.raise_for_status()

    # Para depurar: mostra se deu certo e o tamanho do que veio.
    print(f"  {resp.status_code} {url} ({len(resp.text):,} caracteres)")
    return resp.text


def numero_br(texto: str) -> float:
    """Converte '2,3 m' ou '24 km/h' em número (2.3, 24.0)."""
    achado = re.search(r"-?\d+(?:,\d+)?", texto)
    if achado is None:
        raise ValueError(f"nenhum número em {texto!r}")
    return float(achado.group().replace(",", "."))


def parsear_mares(html: str, ano: int, mes: int) -> list[dict]:
    """Extrai da tábua mensal uma linha por evento de maré.

    Onde cada dado está no HTML (descoberto inspecionando a página):

    - a tabela é <table id="tabla_mareas">. A página tem mais duas cópias
      (tabla_mareas_swipe1/2, versões para celular), que ignoramos;
    - cada dia é uma <tr> com onclick="Day('2026-10-1');" -> a data completa;
    - cada maré do dia é uma <td class="tabla_mareas_marea"> com:
        .tabla_mareas_marea_hora            -> horário ("6:45")
        .tabla_mareas_marea_altura_numero   -> altura ("2,3")
        .tabla_mareas_marea_pleamar         -> maré ALTA (preamar)
        .tabla_mareas_marea_bajamar         -> maré BAIXA (baixa-mar)
      Alguns dias têm só 3 marés: a 4ª célula vem vazia e é pulada;
    - .tabla_mareas_coeficiente_numero -> coeficiente ("96") e nível ("muito alto");
    - a fase da lua é um ícone com classe "icon-hsN": N é a idade da lua em
      dias (0 = lua nova, ~15 = lua cheia).
    """
    sopa = BeautifulSoup(html, "html.parser")
    tabela = sopa.find("table", id="tabla_mareas")
    if tabela is None:
        raise ValueError("tabela de marés não encontrada no HTML")

    linhas = []
    for dia in tabela.find_all("tr", onclick=True):
        achado = re.search(r"Day\('(\d{4})-(\d{1,2})-(\d{1,2})'\)", dia["onclick"])
        if achado is None:
            continue
        a, m, d = (int(x) for x in achado.groups())
        if (a, m) != (ano, mes):
            # A página não é do mês pedido: melhor ignorar do que gravar errado.
            continue
        data = f"{a:04d}-{m:02d}-{d:02d}"

        # Coeficiente: o primeiro "pedaço" de texto é o número, o resto é o nível.
        coef_div = dia.select_one(".tabla_mareas_coeficiente_numero")
        coef_textos = list(coef_div.stripped_strings) if coef_div else []
        coeficiente = int(coef_textos[0]) if coef_textos else None
        coef_nivel = coef_textos[1] if len(coef_textos) > 1 else None

        # Idade da lua a partir da classe "icon-hsN".
        idade_lua = None
        icone = dia.select_one(".tabla_mareas_luna span")
        if icone is not None:
            for classe in icone.get("class", []):
                if re.fullmatch(r"icon-hs\d+", classe):
                    idade_lua = int(classe.removeprefix("icon-hs"))

        for celula in dia.select("td.tabla_mareas_marea"):
            hora = celula.select_one(".tabla_mareas_marea_hora")
            altura = celula.select_one(".tabla_mareas_marea_altura_numero")
            if hora is None or altura is None:
                continue  # célula vazia (dia com só 3 marés)
            tipo = "alta" if celula.select_one(".tabla_mareas_marea_pleamar") else "baixa"
            hh, mm = hora.get_text(strip=True).split(":")
            linhas.append({
                "data": data,
                "hora": f"{int(hh):02d}:{mm}",
                "tipo": tipo,
                "altura_m": numero_br(altura.get_text()),
                "coeficiente": coeficiente,
                "coeficiente_nivel": coef_nivel,
                "idade_lua": idade_lua,
            })
    return linhas


def parsear_previsao(html: str, ano: int) -> list[dict]:
    """Extrai leituras horárias das páginas de previsão de onda e de vento.

    As duas páginas têm o mesmo layout:

    - cada dia é um bloco <div class="ficha">;
    - a data do bloco está em .f_circulo: <span class="dia">10</span> e
      <span class="mes">OUT</span> -- sem o ano, por isso recebemos `ano`;
    - cada hora é um <div class="f_temp_horas"> com três partes:
        1º .f_temp_hora -> horário ("0:00")
        2º .f_temp_hora -> direção ("ESE")
        .f_temp_graf2   -> valor ("0,9 m" nas ondas, "24 km/h" no vento)
    """
    sopa = BeautifulSoup(html, "html.parser")
    linhas = []
    mes_anterior = None

    for bloco in sopa.select(".ficha"):
        dia_tag = bloco.select_one(".f_circulo .dia")
        mes_tag = bloco.select_one(".f_circulo .mes")
        horas = bloco.select(".f_temp_horas")
        if dia_tag is None or mes_tag is None or not horas:
            continue

        mes = MESES_PT[mes_tag.get_text(strip=True).upper()[:3]]
        # Virada de ano: se a previsão vai de DEZ para JAN, o ano aumenta.
        if mes_anterior is not None and mes < mes_anterior:
            ano += 1
        mes_anterior = mes
        data = f"{ano:04d}-{mes:02d}-{int(dia_tag.get_text(strip=True)):02d}"

        for hora in horas:
            partes = hora.select(".f_temp_hora")
            valor_tag = hora.select_one(".f_temp_graf2")
            if len(partes) < 2 or valor_tag is None:
                continue
            texto_valor = valor_tag.get_text(" ", strip=True)  # "0,9 m" / "24 km/h"
            hh, mm = partes[0].get_text(strip=True).split(":")
            linhas.append({
                "data": data,
                "hora": f"{int(hh):02d}:{mm}",
                "direcao": partes[1].get_text(strip=True),
                "valor": numero_br(texto_valor),
                "unidade": texto_valor.split()[-1],
            })
    return linhas


def baixar_mes(ano: int, mes: int) -> str:
    """Baixa a tábua de marés de um mês.

    Ao trocar de mês, o site envia um formulário (POST) com o campo
    "fecha" (data, em espanhol) -- foi isso que vimos no HTML:
    <form id="form_calendario" method="post"><input name="fecha" ...>.
    """
    return baixar_html(URL_BASE, dados_post={"fecha": f"{ano:04d}-{mes:02d}-01"})


def coletar_mares_do_ano(ano: int) -> pd.DataFrame:
    """Junta os 12 meses da tábua de marés de `ano` num DataFrame."""
    linhas = []
    for mes in range(1, 13):
        html = baixar_mes(ano, mes)
        do_mes = parsear_mares(html, ano, mes)
        print(f"    {ano}-{mes:02d}: {len(do_mes)} marés")
        linhas.extend(do_mes)
        time.sleep(PAUSA)
    return pd.DataFrame(linhas)


def salvar(df: pd.DataFrame, nome: str) -> None:
    caminho = DIR_RAW / nome
    df.to_csv(caminho, index=False)
    aviso = "  <-- VAZIO, algo deu errado!" if df.empty else ""
    print(f"==> {nome}: {len(df)} linhas{aviso}\n")


def main(ano: int = 2025) -> None:
    DIR_RAW.mkdir(parents=True, exist_ok=True)
    hoje = datetime.now()

    # 1. Tábua de marés do ano inteiro (12 páginas, uma por mês).
    print(f"[1/4] Marés de {ano}")
    salvar(coletar_mares_do_ano(ano), f"mares_{ano}.csv")

    # 2. Marés do mês corrente e do próximo -- a previsão de 7 dias pode
    #    passar para o mês seguinte, e precisamos da maré desses dias também.
    print("[2/4] Marés para cruzar com a previsão")
    proximo = (hoje.year + hoje.month // 12, hoje.month % 12 + 1)
    linhas = []
    for a, m in [(hoje.year, hoje.month), proximo]:
        linhas.extend(parsear_mares(baixar_mes(a, m), a, m))
        time.sleep(PAUSA)
    salvar(pd.DataFrame(linhas), "mares_previsao.csv")

    # 3. Previsão de ondas.
    print("[3/4] Previsão de ondas")
    ondas = pd.DataFrame(parsear_previsao(baixar_html(URL_ONDAS), hoje.year))
    ondas = ondas.rename(columns={"valor": "altura_onda_m"}).drop(columns="unidade")
    salvar(ondas, "ondas.csv")
    time.sleep(PAUSA)

    # 4. Previsão de vento.
    print("[4/4] Previsão de vento")
    vento = pd.DataFrame(parsear_previsao(baixar_html(URL_VENTO), hoje.year))
    vento = vento.rename(columns={"valor": "vento_kmh"}).drop(columns="unidade")
    salvar(vento, "vento.csv")


if __name__ == "__main__":
    main()
