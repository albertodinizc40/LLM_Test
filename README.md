# 🌍 DW Q&A — Protótipo de análise de dados com IA

App em Streamlit que recebe perguntas em português sobre uma planilha de emissões urbanas
e responde com tabela, gráfico e análise — usando a Groq API (LLM grátis) para gerar SQL.

## Como funciona

```
Pergunta (PT) → Groq gera SQL → DuckDB executa → tabela + gráfico Plotly + análise
```

O LLM **não toca nos números** — ele só escreve a SQL, que o DuckDB executa de verdade.
Isso elimina alucinação numérica e te dá uma SQL auditável (visível na UI) a cada resposta.

---

## 🚀 Rodar localmente

### 1. Pré-requisitos

- Python 3.10+ ([baixar](https://python.org/downloads))
- Conta no Groq (grátis) — pegue uma chave em [console.groq.com/keys](https://console.groq.com/keys)

### 2. Setup

```bash
# Mac/Linux
cd dw_qa_groq
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

```powershell
# Windows
cd dw_qa_groq
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configurar a chave do Groq

Copie o arquivo de exemplo e cole sua chave:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Abra `.streamlit/secrets.toml` em qualquer editor e substitua `gsk_cole_sua_chave_aqui`
pela sua chave real do Groq.

> ⚠️ O arquivo `secrets.toml` está no `.gitignore` — ele **nunca** vai pro Git.

### 4. Rodar

```bash
streamlit run app.py
```

Abre em `http://localhost:8501`. Pra parar: `Ctrl+C` no terminal.

---

## ☁️ Deploy no Streamlit Cloud (grátis, público)

### 1. Subir pro GitHub

- Crie um repositório novo em [github.com/new](https://github.com/new)
- Faça upload de todos os arquivos desta pasta (pode arrastar e soltar pelo site)
- **Não suba** o arquivo `.streamlit/secrets.toml` se ele existir — o `.gitignore` já cuida disso

### 2. Conectar ao Streamlit Cloud

- Entre em [share.streamlit.io](https://share.streamlit.io) e faça login com GitHub
- Clique em "New app"
- Selecione seu repositório, branch `main`, arquivo `app.py`
- Clique em "Deploy"

### 3. Configurar o secret

- No app deployado, vá em **Settings → Secrets**
- Cole:
  ```toml
  GROQ_API_KEY = "sua-chave-do-groq-aqui"
  ```
- Salve. O app reinicia sozinho.

### 4. Pronto

Você recebe uma URL pública tipo `seu-app.streamlit.app` que qualquer pessoa abre.
Toda mudança no GitHub atualiza o app automaticamente.

---

## 📁 Estrutura do projeto

```
dw_qa_groq/
├── app.py                              # Aplicativo Streamlit
├── requirements.txt                    # Dependências Python
├── README.md                           # Este arquivo
├── .gitignore                          # Protege secrets e venv
├── .streamlit/
│   └── secrets.toml.example            # Modelo do secrets (você renomeia)
└── data/
    └── emissions.xlsx                  # Planilha de emissões (vai pro repo)
```

## 🧠 Modelo usado

O app está configurado para `llama-3.3-70b-versatile` — o melhor modelo grátis do Groq
pra esse tipo de tarefa. Pra trocar (mais rápido ou mais barato):

```python
# Em app.py, linha 18:
GROQ_MODEL = "llama-3.1-8b-instant"     # Mais rápido, menos preciso
GROQ_MODEL = "qwen-2.5-32b"             # Bom equilíbrio
```

## 🔒 Sobre privacidade dos dados

- **Conteúdo da planilha NÃO é enviado pro Groq.** Só o schema (nomes de colunas) +
  pergunta + 3 linhas de amostra vão na chamada do LLM.
- **Os dados são processados localmente pelo DuckDB**, no próprio servidor do Streamlit.
- Se quiser zero exposição, troque pela arquitetura com Ollama local (versão anterior do app).

## 📈 Próximos passos

- **Múltiplas tabelas**: ajuste `get_schema()` pra listar todas e mande no prompt
- **Validação de SQL**: bloqueie DROP/DELETE/UPDATE via regex antes de executar
- **Histórico**: salve perguntas em SQLite, mostre últimas no sidebar
- **Avaliação**: monte 30 perguntas com gabarito e meça acerto
