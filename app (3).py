"""
DW Q&A — v2 (Groq + dicionário de dados + raciocínio sobre domínio)

Pipeline:
  Pergunta (PT) -> Groq (com dicionário + cidades + few-shot) -> SQL DuckDB
                                                              -> resultado + gráfico + análise

Diferenças vs v1:
  - Dicionário de dados detalhado em PT pra cada coluna
  - Lista das 82 cidades reais no prompt (resolve "Lisboa" vs "Lisbon")
  - Few-shot examples ensinando PT->EN, sinônimos, ILIKE
  - Detecção de resultado vazio com retry/sugestão
"""
import json
import os
import re
import requests
import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
DATA_PATH = "data/emissions.xlsx"
SHEET = "Sorted 03_2025 All Cities"
TABLE = "cities"

st.set_page_config(page_title="DW Q&A — Emissões urbanas", layout="wide")


def get_api_key():
    try:
        return st.secrets["GROQ_API_KEY"]
    except (KeyError, FileNotFoundError):
        return os.environ.get("GROQ_API_KEY", "")


# ============================================================
# DICIONÁRIO DE DADOS — descreve cada coluna em linguagem humana
# ============================================================
DATA_DICTIONARY = """
DICIONÁRIO DE DADOS — tabela `cities` (82 cidades do mundo, dados C40 sobre emissões de carbono):

1. city (TEXT) — Nome da cidade EM INGLÊS.
   ⚠️ ATENÇÃO: Cidades estão SEMPRE em inglês na base. Traduza nomes em português:
   - "Lisboa" → "Lisbon"        | "Cidade do México" → "Mexico City"
   - "Atenas" → "Athens"        | "Pequim/Beijing" → não está na base
   - "Estocolmo" → "Stockholm"  | "Copenhague" → "Copenhagen"
   - "Roma" → "Rome"            | "Milão" → "Milan"
   - "Tóquio" → "Tokyo"         | "Nova York" → "New York City"
   - "Cidade do Cabo" → "Cape Town"
   ⚠️ Cidades brasileiras MANTÊM nome em português: São Paulo, Rio de Janeiro, Salvador, Curitiba.
   ⚠️ SEMPRE use ILIKE com '%nome%' (não use = exato) para tolerar variações de grafia/acento.

2. region (TEXT) — Região geográfica. Valores fixos (em inglês):
   - "Africa"                          (PT: África)
   - "Central East Asia"               (PT: Ásia Central/Leste — só Hong Kong)
   - "East, Southeast Asia and Oceania" (PT: Ásia Oriental, Sudeste Asiático e Oceania)
   - "Europe"                          (PT: Europa)
   - "Latin America"                   (PT: América Latina)
   - "North America"                   (PT: América do Norte)
   - "South & West Asia"               (PT: Sul e Oeste da Ásia)
   ⚠️ Use ILIKE com '%termo%' para tolerar variações ("europa" → '%urope%').

3. achieved_pev_status (NUMBER 0 ou 1) — Status PEV (Peak Emission Value).
   - 1 = cidade JÁ ATINGIU o pico de emissões e está REDUZINDO desde então (boa notícia)
   - 0 = cidade AINDA NÃO atingiu o pico OU está estagnada no pico (não está reduzindo)
   Sinônimos do usuário: "já passou do pico", "está reduzindo", "atingiu PEV".

4. gn_gs (TEXT 'N' ou 'S') — Classificação Norte/Sul Global.
   - 'N' = Global North (Norte Global — países desenvolvidos)
   - 'S' = Global South (Sul Global — países em desenvolvimento)
   Sinônimos: "norte/sul global", "hemisfério", "norte" → 'N', "sul" → 'S'.

5. peak_data_point (NUMBER, ano 2004-2023) — Ano em que a cidade atingiu o PICO de emissões.
   Sinônimos: "ano do pico", "quando chegou no máximo", "ano de maior emissão".

6. latest_data_point (NUMBER, ano 2016-2023) — Ano do dado MAIS RECENTE disponível pra cidade.
   Sinônimos: "dado mais recente", "última medição", "ano mais atual".

7. city_pct_below_peak_value (NUMBER, %, 0 a 42.42) — % TOTAL de redução de emissões da cidade
   desde o pico até o dado mais recente (REDUÇÃO ACUMULADA).
   Sinônimos: "redução total", "% abaixo do pico", "quanto reduziu", "diminuição",
   "queda de emissões", "redução acumulada".
   ⚠️ Quanto MAIOR esse valor, MELHOR (mais redução).

8. city_annual_avg_pct_reduction_between_peak_latest (NUMBER, %, 0 a 12) — Redução média ANUAL
   de emissões da cidade (entre o pico e o dado mais recente).
   Sinônimos: "redução anual", "ritmo de redução", "redução por ano", "taxa anual".
   ⚠️ DIFERENTE da coluna 7! Esta é POR ANO; aquela é o TOTAL acumulado.

9. regional_average_pct_for_all_cities (NUMBER, %) — Média da redução total das cidades DA REGIÃO
   da cidade. Sinônimos: "média da região", "média regional".

10. global_pct_avg_for_all_cities (NUMBER, %, sempre 12.59) — Média GLOBAL de redução de todas
    as 82 cidades. Sinônimos: "média mundial", "média global".

11. comparative_gn_gs_average (NUMBER, %) — Média de redução do grupo (Norte Global ou Sul Global)
    da cidade. N tem ~18.37%, S tem ~6.54%. Sinônimos: "média do norte/sul global".

REGRAS GERAIS DE SQL:
- SEMPRE use ILIKE com '%termo%' para colunas TEXT (city, region) — nunca = exato.
- Para "top N" / "maior" / "que mais" → ORDER BY ... DESC LIMIT N.
- Para "menor" / "que menos" → ORDER BY ... ASC LIMIT N.
- Filtre valores 0 quando perguntarem sobre "redução" se fizer sentido (cidades com 0 podem ser ruído).
- Para perguntas sobre "norte global" → WHERE gn_gs = 'N'. "Sul global" → gn_gs = 'S'.
- NUNCA use DROP, DELETE, UPDATE, INSERT, ALTER (proibido).
"""

# Lista das 82 cidades — anexada ao prompt pra o LLM "ver" os nomes reais
CITY_LIST_HINT = """
LISTA DAS 82 CIDADES NA BASE (use estes nomes EXATOS quando filtrar por cidade):
Abidjan, Accra, Addis Ababa, Ahmedabad, Amman, Amsterdam, Athens, Auckland, Austin, Bangkok,
Barcelona, Bengaluru, Berlin, Bogotá, Boston, Buenos Aires, Cape Town, Chennai, Chicago,
Copenhagen, Curitiba, Dakar, Dar es Salaam, Delhi NCT, Dhaka North, Dhaka South, Dubai,
Durban (eThekwini), Ekurhuleni, Freetown, Guadalajara, Hanoi, Heidelberg, Ho Chi Minh City,
Hong Kong, Houston, Istanbul, Jakarta, Johannesburg, Kuala Lumpur, Lagos, Lima, Lisbon, London,
Los Angeles, Madrid, Medellín, Melbourne, Mexico City, Miami, Milan, Montréal, Mumbai, Nairobi,
New Orleans, New York City, Oslo, Paris, Philadelphia, Phoenix, Portland, Quezon City, Quito,
Rio de Janeiro, Rome, Rotterdam, Salvador, San Francisco, Santiago, São Paulo, Seattle, Seoul,
Stockholm, Sydney, Tel Aviv-Yafo, Tokyo, Toronto, Tshwane, Vancouver, Warsaw, Washington DC,
Yokohama
"""

# Few-shot: exemplos de pergunta -> SQL boa (ensinam o modelo a raciocinar)
FEW_SHOT_EXAMPLES = """
EXEMPLOS DE PERGUNTA → JSON CORRETO:

Pergunta: "qual cidade mais reduziu emissões?"
{"sql":"SELECT city, region, city_pct_below_peak_value FROM cities WHERE city_pct_below_peak_value > 0 ORDER BY city_pct_below_peak_value DESC LIMIT 10","chart":"bar","x":"city","y":"city_pct_below_peak_value","title":"Top 10 cidades — maior redução total de emissões"}

Pergunta: "quando foi o pico de Lisboa?"
{"sql":"SELECT city, peak_data_point, latest_data_point, city_pct_below_peak_value FROM cities WHERE city ILIKE '%lisbon%'","chart":"table","x":"city","y":"peak_data_point","title":"Pico de emissões — Lisbon"}

Pergunta: "quantas cidades europeias já reduziram mais de 30%?"
{"sql":"SELECT city, city_pct_below_peak_value FROM cities WHERE region ILIKE '%europe%' AND city_pct_below_peak_value > 30 ORDER BY city_pct_below_peak_value DESC","chart":"bar","x":"city","y":"city_pct_below_peak_value","title":"Cidades europeias com redução > 30%"}

Pergunta: "compara norte e sul global"
{"sql":"SELECT CASE WHEN gn_gs='N' THEN 'Norte Global' ELSE 'Sul Global' END AS grupo, ROUND(AVG(city_pct_below_peak_value),2) AS media_reducao, COUNT(*) AS n_cidades FROM cities GROUP BY gn_gs","chart":"bar","x":"grupo","y":"media_reducao","title":"Norte vs Sul Global — redução média de emissões"}

Pergunta: "quantas cidades por região já atingiram o pico?"
{"sql":"SELECT region, SUM(achieved_pev_status) AS ja_passaram_pico, COUNT(*) AS total, ROUND(100.0*SUM(achieved_pev_status)/COUNT(*),1) AS pct FROM cities GROUP BY region ORDER BY pct DESC","chart":"bar","x":"region","y":"pct","title":"% de cidades que já passaram do pico, por região"}

Pergunta: "qual cidade tem a maior taxa anual de redução?"
{"sql":"SELECT city, region, city_annual_avg_pct_reduction_between_peak_latest AS reducao_anual FROM cities WHERE city_annual_avg_pct_reduction_between_peak_latest > 0 ORDER BY reducao_anual DESC LIMIT 10","chart":"bar","x":"city","y":"reducao_anual","title":"Top 10 — maior ritmo anual de redução"}

Pergunta: "cidades brasileiras"
{"sql":"SELECT city, region, peak_data_point, city_pct_below_peak_value FROM cities WHERE city IN ('São Paulo','Rio de Janeiro','Salvador','Curitiba') ORDER BY city_pct_below_peak_value DESC","chart":"bar","x":"city","y":"city_pct_below_peak_value","title":"Cidades brasileiras — redução de emissões"}

Pergunta: "em que ano a maioria atingiu o pico?"
{"sql":"SELECT CAST(peak_data_point AS INTEGER) AS ano_pico, COUNT(*) AS n_cidades FROM cities GROUP BY ano_pico ORDER BY ano_pico","chart":"line","x":"ano_pico","y":"n_cidades","title":"Distribuição do ano de pico de emissões"}
"""


# ============================================================
# DW (DuckDB)
# ============================================================
@st.cache_resource
def get_duckdb():
    con = duckdb.connect(database=":memory:")
    df = pd.read_excel(DATA_PATH, sheet_name=SHEET)

    def norm(c: str) -> str:
        c = c.strip().lower().replace("%", "pct")
        return re.sub(r"[^a-z0-9]+", "_", c).strip("_")

    df.columns = [norm(c) for c in df.columns]
    df = df.dropna(subset=["city"])
    con.register("df_raw", df)
    con.execute(f"CREATE OR REPLACE TABLE {TABLE} AS SELECT * FROM df_raw")
    return con, df


def get_schema_summary(con) -> str:
    rows = con.execute(f"PRAGMA table_info({TABLE})").fetchall()
    return "Colunas: " + ", ".join(f"{r[1]} ({r[2]})" for r in rows)


# ============================================================
# Validação de SQL — bloqueia operações destrutivas
# ============================================================
FORBIDDEN_SQL = re.compile(r"\b(drop|delete|update|insert|alter|truncate|create|attach)\b", re.I)

def validate_sql(sql: str) -> tuple[bool, str]:
    if FORBIDDEN_SQL.search(sql):
        return False, "Operação não permitida (apenas SELECT é aceito)."
    if not re.match(r"\s*select\b", sql, re.I):
        return False, "SQL deve começar com SELECT."
    return True, ""


# ============================================================
# Groq
# ============================================================
def build_system_prompt(schema_summary: str) -> str:
    return f"""Você é um analista de dados sênior que entende profundamente o domínio de \
emissões urbanas de carbono. Sua tarefa: dada uma pergunta em português, gerar uma SQL DuckDB \
correta e o tipo de gráfico adequado.

{DATA_DICTIONARY}

{CITY_LIST_HINT}

{FEW_SHOT_EXAMPLES}

{schema_summary}

FORMATO DA RESPOSTA (RETORNE APENAS ESTE JSON, NADA MAIS):
{{"sql":"SELECT ...","chart":"bar|line|pie|table","x":"col_x","y":"col_y","title":"Título em PT"}}

- "chart":"table" quando o resultado é um único valor ou texto puro.
- Use "bar" pra comparações entre categorias, "line" pra séries no tempo, "pie" pra proporções (poucos itens).
- title sempre em português.
- Sem texto antes ou depois do JSON. Sem comentários. Sem ```json```.
"""


def groq_call(messages: list, api_key: str, temperature: float = 0.1) -> str:
    r = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": GROQ_MODEL, "messages": messages, "temperature": temperature},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def llm_to_sql(question: str, schema_summary: str, api_key: str) -> dict:
    raw = groq_call(
        [
            {"role": "system", "content": build_system_prompt(schema_summary)},
            {"role": "user", "content": question},
        ],
        api_key,
    )
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"Resposta inválida do LLM: {raw[:300]}")
    return json.loads(m.group(0))


def llm_explain(question: str, df_result: pd.DataFrame, sql: str, api_key: str) -> str:
    if df_result.empty:
        prompt = (
            f"O usuário perguntou: '{question}'\n"
            f"A SQL gerada foi:\n{sql}\n\n"
            "Mas não retornou resultados. Em 2-3 frases, em português, explique de forma gentil "
            "por que pode não ter achado nada (possível erro de grafia, cidade não na base, filtro "
            "muito restritivo) e sugira como reformular. NÃO invente dados."
        )
    else:
        prompt = (
            f"Pergunta do usuário: '{question}'\n\n"
            f"Resultado obtido:\n{df_result.head(20).to_string(index=False)}\n\n"
            "Em 2-3 frases curtas e em português claro, responda à pergunta usando esses dados. "
            "Cite valores específicos. NÃO invente números além dos da tabela. "
            "Não fale 'segundo os dados' nem 'a tabela mostra' — vá direto ao ponto."
        )
    return groq_call(
        [
            {"role": "system", "content": "Você é um analista. Responda direto, sem floreios."},
            {"role": "user", "content": prompt},
        ],
        api_key,
        temperature=0.3,
    ).strip()


# ============================================================
# Gráfico
# ============================================================
def render_chart(df: pd.DataFrame, spec: dict):
    chart = spec.get("chart", "table")
    x, y, title = spec.get("x"), spec.get("y"), spec.get("title", "")
    if df.empty:
        st.info("Sem dados pra visualizar.")
        return
    if chart == "table" or x not in df.columns or y not in df.columns:
        st.dataframe(df, use_container_width=True, hide_index=True)
        return
    try:
        if chart == "bar":
            fig = px.bar(df, x=x, y=y, title=title, color=x)
            fig.update_layout(showlegend=False)
        elif chart == "line":
            fig = px.line(df, x=x, y=y, title=title, markers=True)
        elif chart == "pie":
            fig = px.pie(df, names=x, values=y, title=title)
        else:
            st.dataframe(df, use_container_width=True, hide_index=True); return
        fig.update_layout(height=420)
        st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.warning(f"Não consegui gerar gráfico ({e}). Mostrando tabela:")
        st.dataframe(df, use_container_width=True, hide_index=True)


# ============================================================
# UI
# ============================================================
st.title("🌍 DW Q&A — Emissões urbanas")
st.caption(
    "Pergunte em português sobre 82 cidades do mundo. "
    "Dados C40 sobre quando cada cidade atingiu o pico de emissões e quanto reduziu desde então."
)

api_key = get_api_key()
con, df_full = get_duckdb()
schema_summary = get_schema_summary(con)

with st.sidebar:
    st.subheader("⚙️ Configuração")
    st.markdown(f"**Modelo:** `{GROQ_MODEL}`")
    st.markdown(f"**Base:** {len(df_full)} cidades, 11 indicadores")
    if not api_key:
        st.error("GROQ_API_KEY não configurada nos Secrets.")
    else:
        st.success("API key carregada ✓")

    st.markdown("---")
    st.markdown("**💡 Exemplos de perguntas:**")
    examples = [
        "Qual cidade mais reduziu emissões?",
        "Quando foi o pico de Lisboa?",
        "Cidades brasileiras na base",
        "Compara Norte e Sul Global",
        "Cidades europeias com mais de 30% de redução",
        "Qual região tem mais cidades reduzindo emissões?",
        "Quem tem o ritmo anual mais rápido de redução?",
        "Em que ano a maioria atingiu o pico?",
        "Africa vs Europa, qual reduziu mais?",
        "Estocolmo está acima ou abaixo da média global?",
    ]
    for ex in examples:
        if st.button(ex, key=f"ex_{ex}", use_container_width=True):
            st.session_state["question_input"] = ex

    with st.expander("🔧 Dicionário de dados"):
        st.markdown("""
        - **city** — cidade (nomes em inglês)
        - **region** — região geográfica (7 regiões)
        - **achieved_pev_status** — 1=já passou do pico, 0=ainda no/antes do pico
        - **gn_gs** — N=Norte Global, S=Sul Global
        - **peak_data_point** — ano do pico de emissões
        - **latest_data_point** — ano do dado mais recente
        - **city_pct_below_peak_value** — % total de redução desde o pico
        - **city_annual_avg_pct_reduction...** — redução média anual (%)
        - **regional_average_pct...** — média da região
        - **global_pct_avg...** — média global (12.59%)
        - **comparative_gn_gs_average** — média do grupo N ou S
        """)

# Campo de pergunta (controlado por session_state pros botões funcionarem)
if "question_input" not in st.session_state:
    st.session_state["question_input"] = ""

question = st.text_input(
    "Sua pergunta:",
    value=st.session_state["question_input"],
    placeholder="Ex.: qual cidade mais reduziu emissões? / quando foi o pico de Lisboa?",
    key="q_field",
)

if st.button("Perguntar", type="primary") and question:
    if not api_key:
        st.error("Configure GROQ_API_KEY antes de perguntar."); st.stop()

    with st.spinner("🧠 Pensando..."):
        try:
            spec = llm_to_sql(question, schema_summary, api_key)
        except Exception as e:
            st.error(f"Erro ao interpretar pergunta: {e}"); st.stop()

    ok, why = validate_sql(spec["sql"])
    if not ok:
        st.error(f"SQL bloqueada: {why}"); st.stop()

    with st.expander("🔍 SQL gerada (auditável)"):
        st.code(spec["sql"], language="sql")

    try:
        df_result = con.execute(spec["sql"]).df()
    except Exception as e:
        st.error(f"Erro ao executar SQL: {e}")
        with st.spinner("Tentando explicar o erro..."):
            try:
                msg = llm_explain(question, pd.DataFrame(), spec["sql"], api_key)
                st.info(msg)
            except Exception:
                pass
        st.stop()

    if df_result.empty:
        st.warning("⚠️ Nenhum resultado encontrado.")
        with st.spinner("Analisando o motivo..."):
            try:
                msg = llm_explain(question, df_result, spec["sql"], api_key)
                st.info(msg)
            except Exception as e:
                st.caption(f"(não consegui gerar explicação: {e})")
    else:
        col1, col2 = st.columns([1, 1])
        with col1:
            st.subheader("📊 Resultado")
            st.dataframe(df_result, use_container_width=True, hide_index=True)
        with col2:
            st.subheader("📈 Visualização")
            render_chart(df_result, spec)

        with st.spinner("Resumindo..."):
            try:
                st.subheader("💬 Análise")
                st.write(llm_explain(question, df_result, spec["sql"], api_key))
            except Exception as e:
                st.caption(f"(sem análise: {e})")
