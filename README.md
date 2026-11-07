# PCP Industrial

Sistema web de Planejamento e Controle da Produção (PCP), desenvolvido para apoiar o acompanhamento de remessas, produção diária e prazos de entrega em uma fábrica de calçados.

O projeto busca transformar processos de controle realizados manualmente em uma aplicação centralizada, com indicadores e acompanhamento visual da produção.

## 📸 Screenshots

### Visão geral
![Dashboard PCP Industrial](docs/images/Screenshot_4.png)

### Quadro de remessas
![Quadro de remessas](docs/images/Screenshot_6.png)

### Produção diária
![Produção diária](docs/images/Screenshot_1.png)

### Programação
![Programação](docs/images/Screenshot_2.png)

### Importação de Excel
![Importação de Excel](docs/images/Screenshot_3.png)

## 🚀 Funcionalidades

- Dashboard com indicadores de remessas e produção.
- Cadastro, edição, consulta e exclusão lógica de remessas.
- Quadro de remessas com status e prazos de entrega.
- Registro de produção por etapa: Corte, Costura e Montagem.
- Acompanhamento dos pares produzidos e saldo a montar.
- Programação de entregas e visualização de remessas pendentes.
- API para comunicação entre frontend e backend.
- Persistência de dados em banco SQLite.

> **Em desenvolvimento:** a importação de remessas por planilhas Excel ainda não está implementada.

## 🛠️ Tecnologias

**Frontend**
- React
- Vite
- JavaScript
- CSS

**Backend**
- Python
- FastAPI
- Uvicorn
- SQLite

## 🏗️ Arquitetura

- **Frontend:** interface web construída com React e Vite.
- **Backend:** API REST desenvolvida com FastAPI, responsável pela lógica da aplicação e comunicação com o banco de dados SQLite.

## ▶️ Como executar localmente

### Backend

No terminal:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m uvicorn main:app --reload --port 8000
```

A API ficará disponível em `http://127.0.0.1:8000`.

Documentação: `http://127.0.0.1:8000/docs`

### Frontend

Em outro terminal, na raiz do projeto:

```powershell
npm install
npm run dev
```

O Vite exibirá o endereço local da aplicação.

## 📌 Status

MVP em desenvolvimento. As funcionalidades de cadastro e acompanhamento de remessas e produção estão implementadas. Próximas etapas incluem evolução da programação industrial, importação de planilhas e aprimoramento das regras de planejamento.

## 👨‍💻 Autor

**Ruan Robert de Oliveira**

[GitHub](https://github.com/Ruanrobert)