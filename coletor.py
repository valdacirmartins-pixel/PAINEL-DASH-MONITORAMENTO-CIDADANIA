import os
import re
import time
import random
import unicodedata
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

from bs4 import BeautifulSoup
from ddgs import DDGS
from newspaper import Article

from sqlalchemy import create_engine, text
import socket

socket.setdefaulttimeout(15)

# ============================================================
# CONFIG
# ============================================================

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL não encontrada")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=30
)

URL_IBGE = (
    "https://raw.githubusercontent.com/kelvins/"
    "Municipios-Brasileiros/master/csv/municipios.csv"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64)"
    )
}

# ============================================================
# QUERIES
# ============================================================

QUERIES = [
    "morador de rua morto",
    "morador de rua assassinado",
    "morador de rua agredido",
    "morador de rua baleado",
    "morador de rua esfaqueado",
    "morador de rua queimado",
    "morador de rua atropelado",
    "morador de rua encontrado morto",
    "pessoa em situação de rua morta",
    "pessoa em situação de rua assassinada",
    "sem teto morto",
    "sem teto assassinado",
    "violência contra morador de rua",
    "crime contra morador de rua",
    "população em situação de rua",
    "sem-teto",
    "sem teto",
    "pessoas em situação de rua",
    "homem em situação de rua",
    "mulher em situação de rua",
    "criança em situação de rua",
    "adolescente em situação de rua",
    "jovem em situação de rua",
    "pedinte",
    "mendicância",
    "mendigos",
    "mendigo",
    "morador de rua",
    "moradora de rua",
    "pessoa em situação de rua",
    "cidadãos em situação de rua",
    "cidadão em situação de rua",
    "PSR",
    "família em situação de rua",
    "acolhimento população em situação de rua",
    "centro pop",
    "PAR",
]

# ============================================================
# UF
# ============================================================

MAPA_UF = {
    11: "RO",
    12: "AC",
    13: "AM",
    14: "RR",
    15: "PA",
    16: "AP",
    17: "TO",
    21: "MA",
    22: "PI",
    23: "CE",
    24: "RN",
    25: "PB",
    26: "PE",
    27: "AL",
    28: "SE",
    29: "BA",
    31: "MG",
    32: "ES",
    33: "RJ",
    35: "SP",
    41: "PR",
    42: "SC",
    43: "RS",
    50: "MS",
    51: "MT",
    52: "GO",
    53: "DF",
}

# ============================================================
# NORMALIZAÇÃO
# ============================================================

def normalizar_texto(valor):

    texto = str(valor or "").lower()

    texto = unicodedata.normalize("NFKD", texto)

    texto = "".join(
        c for c in texto
        if not unicodedata.combining(c)
    )

    texto = re.sub(r"\s+", " ", texto)

    return texto.strip()

# ============================================================
# BANCO
# ============================================================

def criar_tabela():

    sql = """
    CREATE TABLE IF NOT EXISTS pop_rua (
        id SERIAL PRIMARY KEY,
        titulo TEXT,
        url TEXT UNIQUE,
        municipio TEXT,
        uf VARCHAR(2),
        categoria TEXT,
        latitude DOUBLE PRECISION,
        longitude DOUBLE PRECISION,
        data TIMESTAMP,
        data_coleta TIMESTAMP,
        data_publicacao TIMESTAMP NULL,
        query_origem TEXT,
        criado_em TIMESTAMP DEFAULT NOW()
    );
    """

    with engine.begin() as conn:
        conn.execute(text(sql))

# ============================================================
# INSERT
# ============================================================

def inserir_registro(registro):

    sql = """
    INSERT INTO pop_rua (
        titulo,
        url,
        municipio,
        uf,
        categoria,
        latitude,
        longitude,
        data,
        data_coleta,
        data_publicacao,
        query_origem
    )
    VALUES (
    :titulo,
    :url,
    :municipio,
    :uf,
    :categoria,
    :latitude,
    :longitude,
    :data_publicacao,
    :data_coleta,
    :data_publicacao,
    :query_origem
)
    ON CONFLICT (url) DO NOTHING;
    """
if inseriu:

    print(
        registro["data_publicacao"],
        registro["municipio"],
        registro["titulo"][:60]
    )
    
    with engine.begin() as conn:
        result = conn.execute(text(sql), registro)
        return result.rowcount

# ============================================================
# MUNICÍPIOS
# ============================================================

def carregar_municipios():

    print("📍 Carregando municípios")

    df = pd.read_csv(URL_IBGE)

    df["nome_norm"] = (
        df["nome"]
        .astype(str)
        .apply(normalizar_texto)
    )

    coords = {}

    for _, row in df.iterrows():

        try:

            codigo_uf = int(row["codigo_uf"])

            municipio_norm = row["nome_norm"]

            coords[municipio_norm] = {
                "nome_original": row["nome"],
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                "uf": MAPA_UF.get(codigo_uf, "NI")
            }

        except:
            pass

    lista = sorted(
        coords.keys(),
        key=len,
        reverse=True
    )

    print(f"✅ {len(lista)} municípios")

    return coords, lista

COORDS, LISTA_MUNICIPIOS = carregar_municipios()
REGEX_MUNICIPIOS = re.compile(
    r"\b(" +
    "|".join(
        map(
            re.escape,
            sorted(LISTA_MUNICIPIOS, key=len, reverse=True)
        )
    ) +
    r")\b",
    re.IGNORECASE
)

# ============================================================
# DETECTAR MUNICÍPIO
# ============================================================

def detectar_municipio(texto):

    texto_norm = normalizar_texto(texto)

    match = REGEX_MUNICIPIOS.search(texto_norm)

    if match:
        return normalizar_texto(match.group(0))

    return None

# ============================================================
# CATEGORIA
# ============================================================

def classificar(texto):

    texto = normalizar_texto(texto)

    morte = [
        "morto",
        "morreu",
        "assassinado",
        "homicidio",
        "homicídio",
        "executado",
        "corpo encontrado",
        "óbito",
        "faleceu",
        "morre",
        "cadáver",
        "cadaver",
        "encontrado sem vida",
        "latrocínio",
        "latrocinio"
    ]

    violencia = [
        "agredido",
        "espancado",
        "violencia",
        "violência",
        "baleado",
        "esfaqueado",
        "ataque",
        "ferido",
        "tentativa de homicídio",
        "tentativa de homicidio",
        "ameaçado",
        "ameaçado de morte",
        "roubado",
        "assalto",
        "facada",
        "tiro",
        "tiros",
        "disparo",
        "disparos",
        "queimaduras",
        "incendiado",
        "incêndio criminoso",
    ]

    acidente = [
        "atropelado",
        "acidente",
        "colisão",
        "colisao"
    ]

    if any(x in texto for x in morte):
        return "Morte"

    if any(x in texto for x in violencia):
        return "Violência"

    if any(x in texto for x in acidente):
        return "Acidente"

    return "Outros"

# ============================================================
# EXTRAIR TEXTO FALLBACK
# ============================================================

def extrair_texto_requests(url):

    try:

        response = requests.get(
            url,
            timeout=15,
            headers=HEADERS
        )

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        titulo = ""

        if soup.title:
            titulo = soup.title.text

        paragrafos = soup.find_all("p")

        texto = " ".join(
            p.get_text(" ", strip=True)
            for p in paragrafos
        )

        return titulo, texto

    except:
        return "", ""

# ============================================================
# PROCESSAR
# ============================================================

def processar_noticia(item):

    url, query = item

    try:

        titulo = ""
        texto = ""
        artigo = None

        try:

            artigo = Article(
                url,
                language="pt"
            )

            for tentativa in range(3):

                try:
                    artigo.download()
                    artigo.parse()
                    artigo.nlp()
                    break

                except Exception:
                    time.sleep(2)

            titulo = artigo.title or ""
            texto = artigo.text or ""

        except Exception:
            pass

        # FALLBACK
        if len(texto) < 50:

            titulo2, texto2 = extrair_texto_requests(url)

            if len(texto2) > len(texto):
                titulo = titulo2
                texto = texto2

        if len(texto) < 30:
            return None

        base = f"{titulo}\n{texto}"

        municipio_norm = detectar_municipio(base)

        if municipio_norm:

            info = COORDS.get(municipio_norm)

            municipio = info["nome_original"]
            uf = info["uf"]
            latitude = info["latitude"]
            longitude = info["longitude"]

        else:

            municipio = "Não identificado"
            uf = "NI"
            latitude = None
            longitude = None

        categoria = classificar(base)

        # DATA DA PUBLICAÇÃO
        data_publicacao = None

        if artigo:

            try:
                data_publicacao = artigo.publish_date
            except Exception:
                pass

        if data_publicacao is None:
            data_publicacao = datetime.now()

        return {

            "titulo": titulo[:1000],
            "url": url,
            "municipio": municipio,
            "uf": uf,
            "categoria": categoria,
            "latitude": latitude,
            "longitude": longitude,
            "data_coleta": datetime.now(),
            "data_publicacao": data_publicacao,
            "query_origem": query

        }

    except Exception as e:

        print("Erro processando:", url, e)

        return None
# ============================================================
# BUSCAR URLS
# ============================================================

def buscar_urls():

    urls = {}

    anos = [str(a) for a in range(2010, 2027)]

    meses = [
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro"
    ]

    estados = list(MAPA_UF.values())

    CAPITAIS = [
        "São Paulo",
        "Rio de Janeiro",
        "Salvador",
        "Brasília",
        "Fortaleza",
        "Belo Horizonte",
        "Manaus",
        "Curitiba",
        "Recife",
        "Goiânia",
        "Belém",
        "Porto Alegre",
        "São Luís",
        "Maceió",
        "Natal",
        "João Pessoa",
        "Teresina",
        "Aracaju",
        "Campo Grande",
        "Cuiabá",
        "Palmas",
        "Boa Vista",
        "Macapá",
        "Rio Branco",
        "Porto Velho",
        "Vitória",
        "Florianópolis"
    ]

    queries = []

    for q in QUERIES:

        queries.append(q)

        for ano in anos:
            queries.append(f"{q} {ano}")

        for ano in anos:
            for mes in meses:
                queries.append(f"{q} {mes} {ano}")

        for uf in estados:
            queries.append(f"{q} {uf}")

        for cidade in CAPITAIS:
            queries.append(f"{q} {cidade}")

        for uf in estados:
            for ano in anos:
                queries.append(f"{q} {uf} {ano}")

    queries = list(set(queries))

    print(f"🔎 {len(queries)} queries")

    with DDGS() as ddgs:

        for i, query in enumerate(queries):

            try:

                print(f"🔍 {i+1}/{len(queries)}")

                resultados = ddgs.text(
                    query,
                    region="br-pt",
                    safesearch="off",
                    max_results=300
                )

                novos = 0

                for r in resultados:

                    url = r.get("href")

                    if not url:
                        continue

                    url = url.split("?")[0]

                    if any(x in url.lower() for x in [
                        "youtube",
                        "facebook",
                        "instagram",
                        "twitter",
                        "x.com",
                        "tiktok",
                        ".pdf",
                        "/tag/",
                        "/categoria/",
                        "/search/",
                        "/busca/",
                        "linkedin",
                        "whatsapp",
                        "telegram",
                        "pinterest",
                        "google.com",
                        "webcache",
                        "archive.org",
                        "/feed/",
                        "rss"
                    ]):
                        continue

                    if url not in urls:
                        urls[url] = query
                        novos += 1

                print(f"✅ +{novos}")

            except Exception as e:
                print(e)

            time.sleep(random.uniform(0.2, 0.5))

    print(f"📦 TOTAL URLs: {len(urls)}")

    return urls

# ============================================================
# MAIN
# ============================================================

def main():

    criar_tabela()

    urls = buscar_urls()

    print("=" * 60)
    print(f"TOTAL URLS ENCONTRADAS: {len(urls)}")
    print("=" * 60)

    itens = list(urls.items())

    total = 0
    com_municipio = 0
    sem_municipio = 0
    processadas = 0

    print("INICIANDO PROCESSAMENTO")
    print(f"ITENS: {len(itens)}")

    with ThreadPoolExecutor(max_workers=15) as executor:

        futures = [
            executor.submit(
                processar_noticia,
                item
            )
            for item in itens
        ]

        for future in as_completed(futures):

            registro = future.result()

            if not registro:
                continue

            processadas += 1

            try:

                inseriu = inserir_registro(registro)

                if inseriu:

                    total += 1

                    if registro["municipio"] == "Não identificado":
                        sem_municipio += 1
                    else:
                        com_municipio += 1

                    if total % 100 == 0:
                        print(f"✅ INSERIDOS: {total}")

            except Exception as e:
                print(e)

    print("\n================================")
    print("RESUMO DA COLETA")
    print("================================")
    print(f"URLs encontradas: {len(urls)}")
    print(f"Notícias processadas: {processadas}")
    print(f"Total inserido: {total}")
    print(f"Com município identificado: {com_municipio}")
    print(f"Sem município identificado: {sem_municipio}")

    if total > 0:

        percentual = round(
            (com_municipio / total) * 100,
            2
        )

        print(
            f"Taxa de identificação: "
            f"{percentual}%"
        )

    print("================================\n")


if __name__ == "__main__":
    main()
