"""
Imoveis Tendência — Scraper + Análise com Claude Haiku 4.5
==========================================================
Raspa os anúncios em destaque de Chaves na Mão, VivaReal e ZAP Imóveis
para Joinville/SC e usa o claude-haiku-4-5-20251001 para apresentar
os resultados de forma divertida.

Estratégia por portal
---------------------
• Chaves na Mão  → requests + BeautifulSoup (sem anti-bot agressivo)
• VivaReal       → Playwright headless + interceptação da API JSON interna
• ZAP Imóveis    → Playwright headless + interceptação da API JSON interna
  (VivaReal e ZAP pertencem ao mesmo grupo e compartilham a mesma API)

Dependências:
    pip install anthropic requests beautifulsoup4 playwright
    playwright install chromium
"""

import json
import os
import time
import logging
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

import anthropic
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page, Response

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
        bairro = bairro_tag.get_text(strip=True) if bairro_tag else "não informado"

        area_tag = card.select_one("[class*='area'], [class*='size'], [data-testid*='area']")
        area = area_tag.get_text(strip=True) if area_tag else "não informado"

        quartos_tag = card.select_one("[class*='bedroom'], [class*='room'], [data-testid*='bedroom']")
        quartos = quartos_tag.get_text(strip=True) if quartos_tag else "não informado"

        vagas_tag = card.select_one("[class*='parking'], [class*='garage'], [data-testid*='parking']")
        vagas = vagas_tag.get_text(strip=True) if vagas_tag else "não informado"

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


# ---------------------------------------------------------------------------
# Helpers para VivaReal / ZAP — ambos usam a mesma API interna (GrupoZAP)
# A estratégia é abrir a página com Playwright e interceptar a chamada JSON
# que o próprio site faz para carregar os cards de imóveis.
# ---------------------------------------------------------------------------

# Fragmentos de URL que identificam a API interna do GrupoZAP
_GRUPOZAP_API_PATTERNS = (
    "glue-api",          # endpoint histórico: glue-api.zapimoveis.com.br
    "/v2/listings",      # path principal dos resultados
    "/v3/listings",      # versão alternativa
    "listing-search",    # outro fragmento usado internamente
    "imoveis-search",
)

_PLAYWRIGHT_TIMEOUT = 30_000  # ms


def _imovel_from_grupozap_listing(listing: dict, base_url: str) -> Imovel:
    """
    Converte um item do JSON da API interna do GrupoZAP em um Imovel.

    A API retorna objetos com a estrutura:
      listing.listing  →  dados do imóvel
      listing.account  →  anunciante
    """
    data = listing.get("listing", listing)  # tolera envelopes diferentes

    # --- Título / tipo ---
    tipo = data.get("unitTypes", data.get("unitType", ["Imóvel"]))[0] if isinstance(
        data.get("unitTypes", data.get("unitType")), list
    ) else data.get("unitTypes", data.get("unitType", "Imóvel"))

    titulo = data.get("title") or data.get("description", "")[:80] or tipo

    # --- Localização ---
    address = data.get("address", {})
    bairro_parts = filter(None, [
        address.get("neighborhood"),
        address.get("city"),
        address.get("state"),
    ])
    bairro = ", ".join(bairro_parts) or "não informado"

    # --- Preço ---
    pricing = data.get("pricingInfos", [{}])
    preco_obj = pricing[0] if pricing else {}
    preco_raw = preco_obj.get("price") or preco_obj.get("monthlyRentalTotalPrice", "")
    if preco_raw:
        try:
            preco = f"R$ {float(preco_raw):,.0f}".replace(",", ".")
        except (ValueError, TypeError):
            preco = str(preco_raw)
    else:
        preco = "não informado"

    # --- Área ---
    area_raw = data.get("usableAreas", data.get("totalAreas", [None]))[0] if isinstance(
        data.get("usableAreas", data.get("totalAreas")), list
    ) else None
    area = f"{area_raw} m²" if area_raw else "não informado"

    # --- Quartos ---
    bedrooms = data.get("bedrooms", data.get("bedroomsCount"))
    if isinstance(bedrooms, list):
        bedrooms = bedrooms[0] if bedrooms else None
    quartos = str(bedrooms) if bedrooms is not None else "não informado"

    # --- Vagas ---
    parkings = data.get("parkingSpaces", data.get("garageSpaces"))
    if isinstance(parkings, list):
        parkings = parkings[0] if parkings else None
    vagas = str(parkings) if parkings is not None else "não informado"

    # --- Link ---
    link = data.get("href", data.get("link", ""))
    if link and not link.startswith("http"):
        link = base_url.rstrip("/") + "/" + link.lstrip("/")

    return Imovel(
        titulo=str(titulo)[:120],
        tipo=str(tipo),
        bairro=str(bairro)[:80],
        preco=preco,
        area=area,
        quartos=quartos,
        vagas=vagas,
        url=link,
    )


def _parse_grupozap_response(body: str, base_url: str) -> list[Imovel]:
    """
    Tenta extrair imóveis de uma resposta JSON da API do GrupoZAP.
    Retorna lista vazia se o JSON não tiver o formato esperado.
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []

    # A API pode retornar { search: { result: { listings: [...] } } }
    # ou diretamente { listings: [...] }
    listings = (
        data.get("search", {}).get("result", {}).get("listings")
        or data.get("listings")
        or data.get("result", {}).get("listings")
        or []
    )

    imoveis = []
    for item in listings[:10]:
        try:
            imoveis.append(_imovel_from_grupozap_listing(item, base_url))
        except Exception as exc:  # noqa: BLE001
            log.debug("Erro ao parsear listing: %s", exc)

    return imoveis


def _scrape_grupozap_com_playwright(
    url: str,
    portal_name: str,
    base_url: str,
) -> list[Imovel]:
    """
    Abre a página com Playwright (Chromium headless) e intercepta a primeira
    resposta da API interna do GrupoZAP que contenha listings de imóveis.

    Se a interceptação falhar, faz fallback para parsing do HTML renderizado.
    """
    log.info("Raspando %s via Playwright...", portal_name)
    imoveis: list[Imovel] = []
    api_response_body: Optional[str] = None

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="pt-BR",
                viewport={"width": 1280, "height": 800},
                extra_http_headers={
                    "Accept-Language": "pt-BR,pt;q=0.9",
                },
            )
            page: Page = context.new_page()

            # --- Intercepta respostas de rede ---
            def handle_response(response: Response) -> None:
                nonlocal api_response_body
                if api_response_body:
                    return  # já capturou
                if any(p in response.url for p in _GRUPOZAP_API_PATTERNS):
                    if response.status == 200:
                        try:
                            body = response.text()
                            # Valida se tem listings antes de guardar
                            parsed = json.loads(body)
                            has_listings = (
                                parsed.get("search", {}).get("result", {}).get("listings")
                                or parsed.get("listings")
                                or parsed.get("result", {}).get("listings")
                            )
                            if has_listings:
                                log.info(
                                    "%s: API interceptada → %s",
                                    portal_name, response.url[:80]
                                )
                                api_response_body = body
                        except Exception:  # noqa: BLE001
                            pass

            page.on("response", handle_response)

            # Navega e espera a rede estabilizar
            page.goto(url, wait_until="networkidle", timeout=_PLAYWRIGHT_TIMEOUT)

            # Dá um tempo extra caso a API demore um pouco mais
            if not api_response_body:
                page.wait_for_timeout(3000)

            # --- Fallback: parsing do HTML renderizado ---
            if not api_response_body:
                log.warning(
                    "%s: API não interceptada. Tentando parsing do HTML renderizado.",
                    portal_name,
                )
                html = page.content()
                soup = BeautifulSoup(html, "html.parser")

                # Seletores robustos para a versão atual do GrupoZAP (2025)
                cards = soup.select(
                    "[data-testid='listing-card'], "
                    "article[data-type], "
                    "li[data-testid='result-card'], "
                    "div[class*='ListingCard'], "
                    "div[class*='property-card']"
                )
                log.info("%s: %d cards encontrados no HTML", portal_name, len(cards))

                for card in cards[:10]:
                    titulo_tag = card.select_one(
                        "h2, h3, "
                        "[data-testid='listing-title'], "
                        "[class*='title'], "
                        "[class*='Title']"
                    )
                    titulo = titulo_tag.get_text(strip=True) if titulo_tag else "Imóvel sem título"

                    preco_tag = card.select_one(
                        "[data-testid='listing-price'], "
                        "[class*='price'], "
                        "[class*='Price']"
                    )
                    preco = preco_tag.get_text(strip=True) if preco_tag else "não informado"

                    bairro_tag = card.select_one(
                        "[data-testid='listing-address'], "
                        "[class*='address'], "
                        "[class*='Address'], "
                        "[class*='location'], "
                        "[class*='Location']"
                    )
                    bairro = bairro_tag.get_text(strip=True) if bairro_tag else "não informado"

                    area_tag = card.select_one(
                        "[data-testid*='area'], "
                        "[class*='area'], "
                        "[class*='Area']"
                    )
                    area = area_tag.get_text(strip=True) if area_tag else "não informado"

                    quartos_tag = card.select_one(
                        "[data-testid*='bedroom'], "
                        "[class*='bedroom'], "
                        "[class*='Bedroom'], "
                        "[class*='quarto']"
                    )
                    quartos = quartos_tag.get_text(strip=True) if quartos_tag else "não informado"

                    vagas_tag = card.select_one(
                        "[data-testid*='parking'], "
                        "[class*='parking'], "
                        "[class*='Parking'], "
                        "[class*='garage'], "
                        "[class*='vaga']"
                    )
                    vagas = vagas_tag.get_text(strip=True) if vagas_tag else "não informado"

                    link_tag = card.select_one("a[href]")
                    href = link_tag["href"] if link_tag else ""
                    full_url = href if href.startswith("http") else base_url.rstrip("/") + href

                    imoveis.append(Imovel(
                        titulo=titulo[:120],
                        bairro=bairro[:80],
                        preco=preco,
                        area=area,
                        quartos=quartos,
                        vagas=vagas,
                        url=full_url,
                    ))
            else:
                imoveis = _parse_grupozap_response(api_response_body, base_url)

            browser.close()

    except Exception as exc:  # noqa: BLE001
        log.error("Erro no Playwright ao raspar %s: %s", portal_name, exc)

    log.info("%s: %d imóveis encontrados", portal_name, len(imoveis))
    return imoveis


def scrape_vivareal(url: str) -> list[Imovel]:
    """
    Raspa VivaReal usando Playwright + interceptação da API JSON interna.

    VivaReal bloqueia requests simples com 403. A solução é abrir a página
    com um browser real (Chromium headless) e capturar a resposta da API
    interna que o próprio site consome para renderizar os cards.
    """
    return _scrape_grupozap_com_playwright(
        url=url,
        portal_name="VivaReal",
        base_url="https://www.vivareal.com.br",
    )


def scrape_zapimoveis(url: str) -> list[Imovel]:
    """
    Raspa ZAP Imóveis usando Playwright + interceptação da API JSON interna.

    ZAP e VivaReal pertencem ao mesmo grupo e compartilham a mesma
    infraestrutura de API, então a estratégia é idêntica.
    """
    return _scrape_grupozap_com_playwright(
        url=url,
        portal_name="ZAP Imóveis",
        base_url="https://www.zapimoveis.com.br",
    )


# ---------------------------------------------------------------------------
# Mapa de scrapers
# ---------------------------------------------------------------------------
SCRAPERS = {
    "Chaves na Mão": scrape_chaves_na_mao,
    "VivaReal":      scrape_vivareal,
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
