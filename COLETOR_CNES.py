"""Coletor CNES para o Mapa 2 do CAIS Monitor.

Executar apenas no serviço COLETOR CNES, separado do PAINEL MONITORAMENTO.
Teste: python COLETOR_CNES.py --ufs DF --max-paginas 1
Diagnóstico: python COLETOR_CNES.py --diagnosticar

Gera um CSV importável no Dash; NÃO envia dados automaticamente ao painel.
A consulta depende da disponibilidade e do esquema da API oficial.
"""
import argparse
import csv
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = "https://apidadosabertos.saude.gov.br"
CODES = {'AC':12,'AL':27,'AP':16,'AM':13,'BA':29,'CE':23,'DF':53,'ES':32,'GO':52,
         'MA':21,'MT':51,'MS':50,'MG':31,'PA':15,'PB':25,'PR':41,'PE':26,'PI':22,
         'RJ':33,'RN':24,'RS':43,'RO':11,'RR':14,'SC':42,'SP':35,'SE':28,'TO':17}
REGIONS = {'Norte':'AC AM AP PA RO RR TO','Nordeste':'AL BA CE MA PB PE PI RN SE',
           'Centro-Oeste':'DF GO MS MT','Sudeste':'ES MG RJ SP','Sul':'PR RS SC'}
UF_REGION = {uf:reg for reg,items in REGIONS.items() for uf in items.split()}
COLUMNS = ['ID','Tipo de equipamento','Nome','Instituição responsável','Endereço','Município',
           'UF','Região','CEP','Latitude','Longitude','Situação','Fonte','Atualizado em']


def k(value):
    cleaned = ''.join(c for c in unicodedata.normalize('NFKD', str(value or '').lower())
                      if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', '', cleaned)


def describe(value, limit=450):
    """Resumo seguro da estrutura; não imprime listas inteiras de estabelecimentos."""
    if isinstance(value, dict):
        keys = list(value.keys())
        summary = f"objeto JSON com chaves: {keys[:20]}"
        error_names = {'detail','message','mensagem','error','errors','erro','statuscode'}
        for key, item in value.items():
            if k(key) in error_names and isinstance(item, (str, int, float, bool)):
                summary += f" | {key}: {str(item)[:160]}"
        return summary[:limit]
    if isinstance(value, list):
        return f"lista com {len(value)} itens; primeiro item: {describe(value[0],180) if value else 'nenhum'}"
    return f"{type(value).__name__}: {str(value)[:limit]}"


def query(path, params=None, timeout=25, attempts=3):
    url = ROOT + path + (('?' + urlencode(params)) if params else '')
    for attempt in range(attempts):
        try:
            req = Request(url, headers={
                'Accept':'application/json',
                'User-Agent':'CAIS-Monitor-CNES/1.1 (dados abertos)'
            })
            with urlopen(req, timeout=timeout) as response:
                data = response.read(2_000_001)
                ctype = response.headers.get('Content-Type', '')
            if len(data) > 2_000_000:
                raise ValueError('A API retornou página maior que 2 MB.')
            try:
                return json.loads(data.decode('utf-8-sig'))
            except (ValueError, UnicodeDecodeError):
                excerpt = data[:160].decode('utf-8', errors='replace').replace('\n',' ')
                raise ValueError(f"Resposta não é JSON (Content-Type: {ctype}; início: {excerpt!r}).")
        except HTTPError as exc:
            body = exc.read(350).decode('utf-8', errors='replace').replace('\n',' ')
            err = RuntimeError(f'HTTP {exc.code} em {path}: {body[:200]}')
            if exc.code not in (429,500,502,503,504) or attempt + 1 >= attempts:
                raise err from exc
        except (URLError, TimeoutError, ConnectionError) as exc:
            err = RuntimeError(f'Falha de rede ao consultar {path}: {exc}')
            if attempt + 1 >= attempts:
                raise err from exc
        if attempt + 1 < attempts:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f'Falha ao consultar {path}')


def rows_of(value, level=0):
    """Suporta listas e respostas encapsuladas, sem esconder mensagens de erro da API."""
    if isinstance(value, list):
        return value
    if not isinstance(value, dict) or level > 6:
        raise ValueError('Formato da API não reconhecido: ' + describe(value))
    normalized = {k(key): item for key, item in value.items()}

    # Alguns endpoints retornam um único registro, não uma lista.
    has_id = any(key in normalized for key in ('codigotipounidade','cotipounidade','codigo','id','codigocnes','cnes'))
