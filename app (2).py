"""
DW Q&A — Protótipo com Groq API.

Pergunta em PT -> Groq gera SQL -> DuckDB executa -> tabela + gráfico + resumo do LLM.

Execução:
  pip install -r requirements.txt
  # Crie .streamlit/secrets.toml com:  GROQ_API_KEY = "sua-chave"
  streamlit run app.py
"""
import json
import os
import re
import requests
import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

# ---------- Configuração ----------
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"   # alternativas: "llama-3.1-8b-instant", "qwen-2.5-32b"
DATA_PATH = "data/emissions.xlsx"
SHEET = "Sorted 03_2025 All Cities"
TABLE = "cities"

st.set_page_config(page_title="DW Q&A — Groq", layout="wide")


def get_api_key():
    """Pega a chave do Streamlit secrets (deploy) ou variável de ambiente (local)."""
    try:
        return st.secrets["GROQ_API_KEY"]
    except (KeyError, FileNotFoundError):
        return os.environ.get("GROQ_API_KEY", "")


# ---------- Carga do DW ----------
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


def get_schema(con) -> str:
    rows = con.execute(f"PRAGMA table_info({TABLE})").fetchall()
    cols = [f"  {r[1]} ({r[2]})" for r in rows]
    sample = con.execute(f"SELECT * FROM {TABLE} LIMIT 3").df()
    return (
        f"Tabela: {TABLE}\nColunas:\n" + "\n".join(cols)
        + f"\n\nAmostra:\n{sample.to_string(index=False)}"
    )


# ---------- Groq ----------
SYSTEM_PROMPT = """Você é um analista de dados. Dada uma pergunta em português sobre uma \
tabela DuckDB, gere APENAS um JSON com a query SQL e o tipo de gráfico.

{schema}

Regras:
- Use SOMENTE colunas que existem no schema.
- Sintaxe DuckDB (SQL ANSI padrão).
- Para "top N" use ORDER BY ... LIMIT N.
- Retorne APENAS JSON neste formato exato:
{{"sql":"SELECT ...","chart":"bar|line|pie|table","x":"coluna_x","y":"coluna_y","title":"Título curto"}}
- "chart":"table" quando o resultado é um único valor/texto.
- Sem texto antes ou depois do JSON."""


def groq_call(messages: list, api_key: str) -> str:
    r = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": GROQ_MODEL, "messages": messages, "temperature": 0.1},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def llm_to_sql(question: str, schema: str, api_key: str) -> dict:
    raw = groq_call(
        [
            {"role": "system", "content": SYSTEM_PROMPT.format(schema=schema)},
            {"role": "user", "content": question},
        ],
        api_key,
    )
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"Groq não retornou JSON. Resposta: {raw[:300]}")
    return json.loads(m.group(0))


def llm_explain(question: str, df_result: pd.DataFrame, api_key: str) -> str:
    prompt = (
        "Em 2-3 frases curtas, em português, explique o resultado abaixo respondendo "
        "à pergunta. Não invente números além dos da tabela.\n\n"
        f"Pergunta: {question}\n\nResultado:\n{df_result.head(15).to_string(index=False)}"
    )
    return groq_call(
        [
            {"role": "system", "content": "Você é um analista. Responda direto, sem floreios."},
            {"role": "user", "content": prompt},
        ],
        api_key,
    ).strip()


# ---------- Gráfico ----------
def render_chart(df: pd.DataFrame, spec: dict):
    chart = spec.get("chart", "table")
    x, y, title = spec.get("x"), spec.get("y"), spec.get("title", "")
    if chart == "table" or df.empty or x not in df.columns or y not in df.columns:
        st.dataframe(df, use_container_width=True); return
    if chart == "bar":
        fig = px.bar(df, x=x, y=y, title=title, color=x)
    elif chart == "line":
        fig = px.line(df, x=x, y=y, title=title, markers=True)
    elif chart == "pie":
        fig = px.pie(df, names=x, values=y, title=title)
    else:
        st.dataframe(df, use_container_width=True); return
    fig.update_layout(showlegend=False, height=420)
    st.plotly_chart(fig, use_container_width=True)


# ---------- UI ----------
st.title("🌍 DW Q&A — Emissões urbanas")
st.caption("Pergunte em português. O LLM gera SQL, o DuckDB executa, você vê tabela + gráfico + resumo.")

api_key = get_api_key()
con, df_full = get_duckdb()
schema = get_schema(con)

with st.sidebar:
    st.subheader("⚙️ Configuração")
    st.markdown(f"**Modelo:** `{GROQ_MODEL}`")
    st.markdown(f"**Tabela:** `{TABLE}` ({len(df_full)} linhas)")
    if not api_key:
        st.error("GROQ_API_KEY não configurada.")
        st.markdown("**Local:** crie `.streamlit/secrets.toml` com `GROQ_API_KEY = \"...\"`")
        st.markdown("**Deploy:** configure em Settings → Secrets no Streamlit Cloud")
    else:
        st.success("API key carregada ✓")
    with st.expander("Ver schema"):
        st.code(schema, language="text")
    with st.expander("Sugestões de pergunta"):
        st.markdown("""
        - Qual cidade mais reduziu emissões vs. pico?
        - Mostra a redução média por região
        - Compara norte e sul global
        - Em que ano a maioria das cidades atingiu o pico?
        - Quais cidades europeias reduziram mais de 30%?
        """)

question = st.text_input("Sua pergunta:", placeholder="Ex.: qual cidade mais reduziu emissões vs. pico?")

if st.button("Perguntar", type="primary") and question:
    if not api_key:
        st.error("Configure GROQ_API_KEY antes de perguntar."); st.stop()

    with st.spinner("Gerando SQL via Groq..."):
        try:
            spec = llm_to_sql(question, schema, api_key)
        except Exception as e:
            st.error(f"Erro ao gerar SQL: {e}"); st.stop()

    with st.expander("🔍 SQL gerada (auditável)"):
        st.code(spec["sql"], language="sql")

    try:
        df_result = con.execute(spec["sql"]).df()
    except Exception as e:
        st.error(f"Erro ao executar SQL: {e}"); st.stop()

    col1, col2 = st.columns([1, 1])
    with col1:
        st.subheader("Resultado")
        st.dataframe(df_result, use_container_width=True, hide_index=True)
    with col2:
        st.subheader("Visualização")
        render_chart(df_result, spec)

    with st.spinner("Resumindo..."):
        try:
            st.subheader("💬 Análise")
            st.write(llm_explain(question, df_result, api_key))
        except Exception as e:
            st.warning(f"Sem explicação: {e}")
