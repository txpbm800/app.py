import os

class Config:
    """
    Classe de configuração base para a aplicação Flask.
    Define variáveis de ambiente e configurações essenciais.
    """
    # Chave Secreta para Flask-Login e Flask-WTF
    SECRET_KEY = os.getenv('SECRET_KEY', 'uma_chave_secreta_muito_complexa_e_aleatoria')

    # Configuração da API Key do Gemini
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', 'SUA_CHAVE_DE_API_GEMINI_AQUI')

    # Configuração do Banco de Dados PostgreSQL (Render) ou SQLite (Local)
    DATABASE_URL = os.getenv('DATABASE_URL')
    if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    else:
        # Caminho para o banco de dados SQLite local
        basedir = os.path.abspath(os.path.dirname(__file__))
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(basedir, 'finance.db')
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Configuração de E-mail para Recuperação de Senha
    EMAIL_USERNAME = os.getenv('EMAIL_USERNAME')
    EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
    EMAIL_SERVER = os.getenv('EMAIL_SERVER', 'smtp.gmail.com')
    EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))

    # DEBUG: Imprimir valores das variáveis de ambiente de e-mail ao carregar a configuração
    print(f"DEBUG CONFIG: EMAIL_USERNAME: {'SET' if EMAIL_USERNAME else 'NOT SET'}")
    print(f"DEBUG CONFIG: EMAIL_PASSWORD: {'SET' if EMAIL_PASSWORD else 'NOT SET'}")
    print(f"DEBUG CONFIG: EMAIL_SERVER: {EMAIL_SERVER}")
    print(f"DEBUG CONFIG: EMAIL_PORT: {EMAIL_PORT}")

