import os
from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate # Importar Flask-Migrate
from config import Config
from models import db, User
from routes.main_routes import main_bp
from routes.auth_routes import auth_bp
from routes.budget_routes import budget_bp
from routes.goal_routes import goal_bp
from routes.account_routes import account_bp
from routes.report_routes import report_bp
from services import create_default_data_for_user

app = Flask(__name__)
app.config.from_object(Config) # Carrega as configurações da classe Config

# Inicializa Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login' # Define a view de login

@login_manager.user_loader
def load_user(user_id):
    """Função de callback do Flask-Login para carregar um usuário."""
    return User.query.get(int(user_id))

# Inicializa Flask-Migrate
migrate = Migrate()

# Registra os Blueprints
app.register_blueprint(main_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(budget_bp)
app.register_blueprint(goal_bp)
app.register_blueprint(account_bp)
app.register_blueprint(report_bp)

# --- INICIALIZAÇÃO DO BANCO DE DADOS ---
# Associar db e migrate ao app dentro do contexto da aplicação
with app.app_context():
    db.init_app(app) # Associar SQLAlchemy ao app
    migrate.init_app(app, db) # Associar Flask-Migrate ao app e db
    db.create_all() # Cria as tabelas se não existirem (para SQLite local ou primeira vez)

    # Opcional: Criar dados padrão para um usuário se o banco estiver vazio
    # Isso é útil para o primeiro uso ou para testes.
    # Você pode remover isso em produção ou adicionar uma lógica para verificar se o admin existe.
    # if not User.query.first():
    #     print("Nenhum usuário encontrado. Criando usuário de exemplo 'testuser'...")
    #     test_user = User(username='testuser', email='test@example.com')
    #     test_user.set_password('password123')
    #     db.session.add(test_user)
    #     db.session.commit()
    #     create_default_data_for_user(test_user)
    #     print("Usuário de exemplo 'testuser' criado com dados padrão.")


if __name__ == '__main__':
    # Obtém a porta do ambiente (para implantação como Render) ou usa 5000 por padrão
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False) # Em produção, defina debug=False
