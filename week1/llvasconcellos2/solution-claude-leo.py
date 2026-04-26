"""
Imoveis Tendência — Scraper + Análise com Claude Haiku 4.5
==========================================================
Raspa os anúncios em destaque de Chaves na Mão, VivaReal e ZAP Imóveis
para Joinville/SC e usa o claude-haiku-4-5-20251001 para apresentar
os resultados de forma divertida.

Dependências:
    pip install anthropic requests beautifulsoup4 playwright
    playwright install chromium
"""

import os
import time
import logging
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

import anthropic
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuração de logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 2048

URLS = {
    "Chaves na Mão": "https://www.chavesnamao.com.br/imoveis/sc-joinville/",
    "VivaReal":      "https://www.vivareal.com.br/venda/santa-catarina/joinville/?order=relevance",
    "ZAP Imóveis":   "https://www.zapimoveis.com.br/venda/imoveis/sc+joinville/?order=relevance",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """
Você é o **IMOVELKIT** 🏠✨ — o assistente mais animado do mercado imobiliário brasileiro!

Sua missão é analisar o conteúdo raspado de portais de imóveis (Chaves na Mão, VivaReal e ZAP Imóveis)
e apresentar os imóveis em tendência de forma divertida, como se fosse um apresentador de programa
de TV de decoração misturado com um influencer de imóveis no TikTok.

## Sua personalidade:
- Entusiasmado e irreverente, mas sem perder a precisão nas informações
- Usa emojis com moderação para dar vida ao texto 🏡💰🔥
- Faz analogias engraçadas (ex: "esse apartamento tem mais área verde que o jardim da minha tia")
- Comenta o preço com bom humor (ex: "só R$ 850k — praticamente de graça, né?!")
- Destaca as "bizarrices" dos anúncios quando aparecerem (fotos estranhas, descrições exageradas, etc.)

## O que você deve fazer:
1. Identificar e listar os imóveis que aparecem como destaque/tendência em cada site
2. Para cada imóvel apresentar: tipo, bairro, metragem, quartos, vagas, preço
3. Comparar os destaques entre os três portais quando possível
4. Apontar qual parece ser o "imóvel da vez" em Joinville com uma justificativa bem-humorada
5. Fechar com um resumo das tendências do mercado local baseado nos dados coletados

## Formato da resposta:
- Separe por portal com um título claro
- Use listas para facilitar a leitura
- Termine com uma seção "🏆 Veredito Final do IMOVELKIT"

## Regras importantes:
- Nunca invente dados — use APENAS o que foi extraído do site
- Se um campo estiver ausente nos dados, diga "não informado" com bom humor
- Se o conteúdo recebido estiver vazio ou com erro, avise de forma simpática que não conseguiu as infos
- Mantenha o tom leve mesmo quando os dados forem entediantes

Lembre-se: você transforma dados secos de imóvel em entretenimento imobiliário! 🎬
""".strip()


# ---------------------------------------------------------------------------
# Dataclass para representar um imóvel
# ---------------------------------------------------------------------------
@dataclass
class Imovel:
    titulo: str
    bairro: str = "não informado"
    tipo: str = "não informado"
    preco: str = "não informado"
    area: str = "não informado"
    quartos: str = "não informado"
    vagas: str = "não informado"
    url: str = ""

    def __str__(self) -> str:
        return (
            f"• {self.titulo}\n"
            f"  Tipo: {self.tipo} | Bairro: {self.bairro}\n"
            f"  Área: {self.area} | Quartos: {self.quartos} | Vagas: {self.vagas}\n"
            f"  Preço: {self.preco}\n"
            f"  Link: {self.url or 'não disponível'}"
        )


# ---------------------------------------------------------------------------
# Scrapers por portal
# ---------------------------------------------------------------------------

def _get_soup(url: str, timeout: int = 15) -> Optional[BeautifulSoup]:
    """Faz a requisição HTTP e retorna um BeautifulSoup, ou None em caso de erro."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as exc:
        log.warning("Falha ao acessar %s: %s", url, exc)
        return None


def scrape_chaves_na_mao(url: str) -> list[Imovel]:
    """Raspa os primeiros anúncios da listagem do Chaves na Mão."""
    log.info("Raspando Chaves na Mão...")
    soup = _get_soup(url)
    if not soup:
        return []

    imoveis: list[Imovel] = []

    # Cards de anúncios — seletores baseados na estrutura atual do site
    cards = soup.select("a.card-listing, div.card-listing a, article.listing-item")
    if not cards:
        # Fallback: qualquer link que leve para /imovel/
        cards = [a for a in soup.find_all("a", href=True) if "/imovel/" in a["href"]]

    seen = set()
    for card in cards[:10]:
        href = card.get("href", "")
        if not href or href in seen:
            continue
        seen.add(href)

        titulo = (
            card.get("title")
            or card.select_one("h2, h3, .card-title, .listing-title")
            and card.select_one("h2, h3, .card-title, .listing-title").get_text(strip=True)
            or "Imóvel sem título"
        )

        preco_tag = card.select_one(".price, .card-price, [class*='price']")
        preco = preco_tag.get_text(strip=True) if preco_tag else "não informado"

        bairro_tag = card.select_one(".address, .card-address, [class*='address'], [class*='location']")
        bairro = bairro_tag.get_text(strip=True, separator=" - ") if bairro_tag else "não informado"

        area_tag = card.select_one(".style_list__XnasM>:nth-child(1)")
        area = "".join(area_tag.find_all(string=True, recursive=False)).strip() if area_tag else "não informado"

        quartos_tag = card.select_one(".style_list__XnasM>:nth-child(2)")
        quartos = "".join(quartos_tag.find_all(string=True, recursive=False)).strip() if quartos_tag else "não informado"

        vagas_tag = card.select_one(".style_list__XnasM>:nth-child(4)")
        vagas = "".join(vagas_tag.find_all(string=True, recursive=False)).strip() if vagas_tag else "não informado"

        full_url = href if href.startswith("http") else f"https://www.chavesnamao.com.br{href}"

        imoveis.append(Imovel(
            titulo=titulo[:120],
            bairro=bairro[:80],
            preco=preco,
            area=area,
            quartos=quartos,
            vagas=vagas,
            url=full_url,
        ))

    log.info("Chaves na Mão: %d imóveis encontrados", len(imoveis))
    return imoveis


def scrape_vivareal(url: str) -> list[Imovel]:
    """
    Tenta raspar VivaReal via requests. Se bloqueado (403/empty),
    retorna lista vazia com aviso.
    """
    log.info("Raspando VivaReal...")
    soup = _get_soup(url)
    if not soup:
        log.warning("VivaReal bloqueou o acesso (anti-bot). Retornando lista vazia.")
        return []

    imoveis: list[Imovel] = []

    cards = soup.select(
        "article[data-type='property'], "
        "li.results-list__item, "
        "[data-testid='listing-card']"
    )

    for card in cards[:10]:
        titulo_tag = card.select_one("h2, h3, [data-testid='listing-title'], .property-card__title")
        titulo = titulo_tag.get_text(strip=True) if titulo_tag else "Imóvel sem título"

        preco_tag = card.select_one("[data-testid='listing-price'], .property-card__price, .price__item")
        preco = preco_tag.get_text(strip=True) if preco_tag else "não informado"

        bairro_tag = card.select_one(
            "[data-testid='listing-address'], .property-card__address, .address"
        )
        bairro = bairro_tag.get_text(strip=True) if bairro_tag else "não informado"

        area_tag = card.select_one("[data-testid='listing-area'], .property-card__detail-area")
        area = area_tag.get_text(strip=True) if area_tag else "não informado"

        quartos_tag = card.select_one(
            "[data-testid='listing-bedrooms'], .property-card__detail-room"
        )
        quartos = quartos_tag.get_text(strip=True) if quartos_tag else "não informado"

        vagas_tag = card.select_one(
            "[data-testid='listing-parking'], .property-card__detail-garage"
        )
        vagas = vagas_tag.get_text(strip=True) if vagas_tag else "não informado"

        link_tag = card.select_one("a[href]")
        href = link_tag["href"] if link_tag else ""
        full_url = href if href.startswith("http") else f"https://www.vivareal.com.br{href}"

        imoveis.append(Imovel(
            titulo=titulo[:120],
            bairro=bairro[:80],
            preco=preco,
            area=area,
            quartos=quartos,
            vagas=vagas,
            url=full_url,
        ))

    log.info("VivaReal: %d imóveis encontrados", len(imoveis))
    return imoveis


def scrape_zapimoveis(url: str) -> list[Imovel]:
    """
    Tenta raspar ZAP Imóveis via requests. Mesma ressalva do VivaReal
    (mesmo grupo, proteção anti-bot semelhante).
    """
    log.info("Raspando ZAP Imóveis...")
    soup = _get_soup(url)
    if not soup:
        log.warning("ZAP Imóveis bloqueou o acesso (anti-bot). Retornando lista vazia.")
        return []

    imoveis: list[Imovel] = []

    cards = soup.select(
        "div.listings-wrapper > section > ul > li[data-cy='rp-property-cd']"
    )

    for card in cards[:10]:
        titulo_tag = card.select_one("a")
        titulo = titulo_tag.get('title') if titulo_tag else "Imóvel sem título"

        preco_tag = card.select_one(
            "[data-cy='rp-cardProperty-pricePeriod-txt']"
        )
        preco = preco_tag.get_text(strip=True) if preco_tag else "não informado"

        localizacao_tags = card.select(
            "[data-cy='rp-cardProperty-location-txt'], [data-cy='rp-cardProperty-street-txt']",
            limit=2,
        )
        localizacao_partes = [tag.get_text(strip=True) for tag in localizacao_tags if tag]
        bairro = " - ".join(localizacao_partes) if localizacao_partes else "não informado"

        area_tag = card.select_one("[data-cy='rp-cardProperty-propertyArea-txt'] > h3")
        area = "".join(area_tag.find_all(string=True, recursive=False)).strip() if area_tag else "não informado"

        quartos_tag = card.select_one("[data-cy='rp-cardProperty-bedroomQuantity-txt'] > h3")
        quartos = "".join(quartos_tag.find_all(string=True, recursive=False)).strip() if quartos_tag else "não informado"

        vagas_tag = card.select_one("[data-cy='rp-cardProperty-parkingSpacesQuantity-txt'] > h3")
        vagas = "".join(vagas_tag.find_all(string=True, recursive=False)).strip() if vagas_tag else "não informado"

        link_tag = card.select_one("a[href]")
        href = link_tag["href"] if link_tag else ""
        full_url = href if href.startswith("http") else f"https://www.zapimoveis.com.br{href}"

        imoveis.append(Imovel(
            titulo=titulo[:120],
            bairro=bairro[:80],
            preco=preco,
            area=area,
            quartos=quartos,
            vagas=vagas,
            url=full_url,
        ))

    log.info("ZAP Imóveis: %d imóveis encontrados", len(imoveis))
    return imoveis


# ---------------------------------------------------------------------------
# Mapa de scrapers
# ---------------------------------------------------------------------------
SCRAPERS = {
    "Chaves na Mão": scrape_chaves_na_mao,
    "VivaReal":      scrape_zapimoveis,
    "ZAP Imóveis":   scrape_zapimoveis,
}


# ---------------------------------------------------------------------------
# Coleta todos os portais
# ---------------------------------------------------------------------------
def coletar_imoveis(urls: dict[str, str] | None = None) -> dict[str, list[Imovel]]:
    """
    Percorre todos os portais e retorna um dicionário
    { nome_portal: [Imovel, ...] }.
    """
    urls = urls or URLS
    resultado: dict[str, list[Imovel]] = {}

    for portal, url in urls.items():
        scraper = SCRAPERS.get(portal)
        if scraper:
            resultado[portal] = scraper(url)
        else:
            log.warning("Scraper não encontrado para: %s", portal)
            resultado[portal] = []

        time.sleep(1.5)  # pausa educada entre requisições

    return resultado


# ---------------------------------------------------------------------------
# Formata o conteúdo coletado para enviar ao Claude
# ---------------------------------------------------------------------------
def formatar_para_claude(dados: dict[str, list[Imovel]]) -> str:
    """Converte o dicionário de imóveis em texto estruturado para o prompt."""
    partes: list[str] = []

    for portal, imoveis in dados.items():
        partes.append(f"=== {portal.upper()} ===")
        if not imoveis:
            partes.append(
                "(Nenhum imóvel coletado — o site pode ter bloqueado o acesso "
                "ou a estrutura HTML mudou. Comente isso com humor!)"
            )
        else:
            for i, imovel in enumerate(imoveis, 1):
                partes.append(f"{i}. {imovel}")
        partes.append("")

    return "\n".join(partes)


# ---------------------------------------------------------------------------
# Chama o Claude Haiku 4.5
# ---------------------------------------------------------------------------
def analisar_com_claude(
    conteudo: str,
    api_key: str | None = None,
) -> str:
    """
    Envia o conteúdo coletado para o Claude Haiku 4.5 e retorna a análise.

    Args:
        conteudo: Texto formatado com os imóveis de todos os portais.
        api_key:  Chave da API Anthropic. Se None, lê de ANTHROPIC_API_KEY.

    Returns:
        Texto da análise gerada pelo modelo.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    
    if not key:
        raise ValueError(
            "Chave da API não encontrada. "
            "Defina ANTHROPIC_API_KEY ou passe api_key= para a função."
        )

    client = anthropic.Anthropic(api_key=key)

    user_message = (
        "Aqui estão os imóveis em destaque/tendência coletados hoje nos portais "
        "de Joinville/SC. Analise e apresente no seu estilo animado!\n\n"
        + conteudo
    )

    log.info("Enviando dados para o Claude Haiku 4.5...")

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    return response.content[0].text


# ---------------------------------------------------------------------------
# Função principal (pipeline completo)
# ---------------------------------------------------------------------------
def buscar_imoveis_tendencia(
    api_key: str | None = None,
    urls: dict[str, str] | None = None,
    imprimir: bool = True,
) -> str:
    """
    Pipeline completo:
      1. Raspa os portais
      2. Formata os dados
      3. Envia ao Claude Haiku 4.5
      4. Retorna (e opcionalmente imprime) a análise

    Args:
        api_key:   Chave da API Anthropic (opcional se já estiver em env).
        urls:      Dicionário { portal: url } personalizado (opcional).
        imprimir:  Se True, imprime o resultado no console.

    Returns:
        String com a análise do Claude.
    """
    log.info("🏠 Iniciando busca de imóveis tendência em Joinville/SC...")

    # 1. Coleta
    dados = coletar_imoveis(urls)

    # 2. Formata
    conteudo = formatar_para_claude(dados)
    log.info("Conteúdo coletado:\n%s", conteudo)

    # 3. Analisa com Claude
    analise = analisar_com_claude(conteudo, api_key=api_key)

    # 4. Saída
    if imprimir:
        separador = "=" * 60
        print(f"\n{separador}")
        print("🏠  IMOVELKIT — TENDÊNCIAS DE JOINVILLE/SC")
        print(separador)
        print(analise)
        print(separador)

    return analise


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Coloque sua chave aqui ou exporte ANTHROPIC_API_KEY no terminal:
    # export ANTHROPIC_API_KEY="sk-ant-..."
    load_dotenv(override=True)
    api_key = os.getenv('ANTHROPIC_API_KEY')
    buscar_imoveis_tendencia(api_key=api_key)
    # scrape = scrape_chaves_na_mao(URLS["Chaves na Mão"])
    # for item in scrape:
    #     print(item)
    #     print('===============')
