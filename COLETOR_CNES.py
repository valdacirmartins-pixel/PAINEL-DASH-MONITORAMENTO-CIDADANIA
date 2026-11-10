"""Coleta nacional de estabelecimentos de saúde pela API pública CNES.

Executar separadamente do app web (ex.: máquina local/terminal Railway):
  python coletar_cnes_nacional.py --max-paginas 3
  python coletar_cnes_nacional.py --max-paginas 0  # todas as páginas, pode ser grande

Produz CSV importável no Mapa 2. Não cria pontos fictícios e não altera o cadastro.
A API pública pode sofrer limites, falhas e alterações de esquema.
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
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT='https://apidadosabertos.saude.gov.br'
CODES={'AC':12,'AL':27,'AP':16,'AM':13,'BA':29,'CE':23,'DF':53,'ES':32,'GO':52,
       'MA':21,'MT':51,'MS':50,'MG':31,'PA':15,'PB':25,'PR':41,'PE':26,'PI':22,
       'RJ':33,'RN':24,'RS':43,'RO':11,'RR':14,'SC':42,'SP':35,'SE':28,'TO':17}
REGIONS={'Norte':'AC AM AP PA RO RR TO','Nordeste':'AL BA CE MA PB PE PI RN SE',
         'Centro-Oeste':'DF GO MS MT','Sudeste':'ES MG RJ SP','Sul':'PR RS SC'}
UF_REGION={uf:reg for reg,items in REGIONS.items() for uf in items.split()}
COLUMNS=['ID','Tipo de equipamento','Nome','Instituição responsável','Endereço','Município',
         'UF','Região','CEP','Latitude','Longitude','Situação','Fonte','Atualizado em']


def k(text):
    return re.sub('[^a-z0-9]+','', ''.join(c for c in unicodedata.normalize('NFKD',str(text or '').lower())
                    if not unicodedata.combining(c)))


def query(path, params=None, timeout=25, attempts=3):
    url=ROOT+path+('?' + urlencode(params) if params else '')
    for attempt in range(attempts):
        try:
            with urlopen(Request(url,headers={'Accept':'application/json',
                       'User-Agent':'CAIS-Monitor-Mapeamento/1.0 (consulta de bases públicas)'}),timeout=timeout) as resp:
                raw=resp.read(2_000_001)
            if len(raw)>2_000_000: raise ValueError('Resposta excede limite por página')
            return json.loads(raw)
        except Exception:
            if attempt+1==attempts: raise
            time.sleep(2*(attempt+1))


def rows_of(value):
    if isinstance(value,list): return value
    if isinstance(value,dict):
        for key in ('estabelecimentos','data','dados','items','resultados','results','records','conteudo'):
            if isinstance(value.get(key),list): return value[key]
        for sub in value.values():
            if isinstance(sub,dict):
                try:return rows_of(sub)
                except ValueError:pass
    raise ValueError('Formato da API não reconhecido; examine a resposta antes de continuar.')


def canonical_types(raw):
    tipos={}
    for x in rows_of(raw):
        if not isinstance(x,dict):continue
        d={k(a):v for a,v in x.items()}
        name=str(next((d.get(a) for a in ('descricao','descricaotipo','descricaotipounidade','nome','tipo') if d.get(a)),''))
        code=next((d.get(a) for a in ('codigotipounidade','codigotipo','codigo','id','cotipounidade') if d.get(a) is not None),None)
        if code is None: continue
        n=k(name)
        if 'hospital' in n: tipo='Hospital'
        elif 'centrodesaude' in n or 'unidadebasica' in n or n.startswith('posto'):
            tipo='UBS'
        elif 'prontoatendimento' in n or n.startswith('upa'):
            tipo='UPA'
        elif 'atencaopsicossocial' in n or n.startswith('caps'):
            tipo='CAPS/CAPS AD'
        else:continue
        try: tipos[int(code)]=(tipo,name)
        except(ValueError,TypeError):continue
    if not tipos: raise ValueError('Não foi possível identificar tipos UBS, UPA e hospital na API.')
    return tipos


def get(d,*names):
    for name in names:
        if (v:=d.get(k(name))) not in (None,''):return str(v).strip()
    return ''


def normalize(row,uf,tipo):
    d={k(key):value for key,value in row.items()}
    ident=get(d,'codigo_cnes','cnes','codigo_estabelecimento','codigocnes','co_cnes')
    nome=get(d,'nome_fantasia','nomeFantasia','nome','no_fantasia','nome_estabelecimento')
    if not ident or not nome:return None
    lat=get(d,'latitude','nu_latitude','lat')
    lon=get(d,'longitude','nu_longitude','long','lon')
    try:
        lat=float(lat.replace(',','.')); lon=float(lon.replace(',','.'))
        if not (-34<=lat<=6 and -75<=lon<=-32):lat=lon=None
    except(ValueError,TypeError):lat=lon=None
    endereco=get(d,'endereco','logradouro','no_logradouro')
    numero=get(d,'numero','numero_estabelecimento','nu_numero')
    if numero and numero not in endereco: endereco+=', '+numero
    return {'ID':'CNES-'+ident,'Tipo de equipamento':tipo,'Nome':nome,
           'Instituição responsável':get(d,'razao_social','nome_razao_social','no_razao_social'),
           'Endereço':endereco,'Município':get(d,'municipio','nome_municipio','cidade'),
           'UF':uf,'Região':UF_REGION[uf],'CEP':get(d,'cep'),
           'Latitude':'' if lat is None else lat,'Longitude':'' if lon is None else lon,
           'Situação':'Cadastro ativo no CNES — confirmar funcionamento e atendimento ao público',
           'Fonte':'CNES/DATASUS — https://apidadosabertos.saude.gov.br/cnes/estabelecimentos',
           'Atualizado em':datetime.now(timezone.utc).date().isoformat()}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--max-paginas',type=int,default=3,
                   help='Páginas de 20 resultados por tipo e UF; 0 para todas')
    p.add_argument('--ufs',nargs='*',default=list(CODES))
    p.add_argument('--saida',default='CNES_Rede_Nacional_Para_Dash.csv')
    args=p.parse_args()
    if args.max_paginas<0: p.error('max-paginas deve ser >=0')
    if any(uf not in CODES for uf in args.ufs):p.error('UF inválida')
    tipos=canonical_types(query('/cnes/tipounidades'))
    dest=Path(args.saida)
    # Reaproveita resultados anteriores para não perder coleta em interrupções.
    saved=set()
    if dest.is_file():
        with dest.open(encoding='utf-8-sig',newline='') as f:
            for r in csv.DictReader(f,delimiter=';'):saved.add((r.get('ID'),r.get('Tipo de equipamento')))
    new_file=not dest.is_file()
    with dest.open('a',encoding='utf-8-sig' if new_file else 'utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=COLUMNS,delimiter=';')
        if new_file:writer.writeheader()
        for uf in args.ufs:
            for cod,(tipo,descricao) in tipos.items():
                page=0
                while args.max_paginas==0 or page<args.max_paginas:
                    try:
                        raw=query('/cnes/estabelecimentos',{
                            'codigo_uf':CODES[uf],'codigo_tipo_unidade':cod,
                            'status':1,'limit':20,'offset':page})
                        rows=rows_of(raw)
                    except Exception as err:
                        print(f'FALHA {uf}, {descricao}, página {page}: {err}',file=sys.stderr)
                        break
                    if not rows:break
                    n=0
                    for x in rows:
                        if not isinstance(x,dict):continue
                        rec=normalize(x,uf,tipo)
                        if rec is None or (rec['ID'],rec['Tipo de equipamento']) in saved:continue
                        writer.writerow(rec);saved.add((rec['ID'],rec['Tipo de equipamento']));n+=1
                    handle.flush()
                    print(f'{uf} | {tipo} | página {page}: {n} novos; total acumulado {len(saved)}')
                    page+=1
                    if len(rows)<20:break
                    time.sleep(.5)
    print(f'Concluído: {dest}. Conteúdo deve ser validado antes de uso institucional.')

if __name__=='__main__':main()
