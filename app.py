# app.py

from flask import Flask, render_template, request, redirect, url_for, flash # Adicionado flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user # Importações para Flask-Login
from werkzeug.security import generate_password_hash, check_password_hash # Para hash de senhas
import datetime
import os

app = Flask(__name__)

# Configuração da Chave Secreta para Flask-Login e Flask-WTF
# ALERTA DE SEGURANÇA: Em produção, use uma chave realmente secreta e complexa,
# lida de uma variável de ambiente (ex: app.config['SECRET_KEY'] = os.getenv('SECRET_KEY'))
app.config['SECRET_KEY'] = 'uma_chave_secreta_muito_complexa_e_aleatoria' # Substitua por uma chave forte!

# Configuração do Banco de Dados SQLite
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'finance.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager() # Inicializa o Flask-Login
login_manager.init_app(app) # Conecta o Flask-Login à sua aplicação Flask
login_manager.login_view = 'login' # Define a rota para a página de login se o usuário não estiver autenticado

# --- Definição dos Modelos do Banco de Dados (ORM) ---

# Modelo para Usuários
class User(UserMixin, db.Model): # UserMixin fornece implementações padrão para métodos necessários ao Flask-Login
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False) # Para armazenar a senha com hash
    
    # Relacionamento com transações e contas (opcional, mas bom para clareza)
    transactions = db.relationship('Transaction', backref='user', lazy=True)
    bills = db.relationship('Bill', backref='user', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password) # Cria o hash da senha

    def check_password(self, password):
        return check_password_hash(self.password_hash, password) # Verifica a senha

    def __repr__(self):
        return f"<User {self.username}>"

# Função do Flask-Login para carregar o usuário pelo ID
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Modelo para Transações (modificado para incluir user_id)
class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.String(10), nullable=False)
    type = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False) # Chave estrangeira para o usuário

    def __repr__(self):
        return f"<Transaction {self.description} - {self.amount}>"

# Modelo para Contas a Pagar (modificado para incluir user_id)
class Bill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    dueDate = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False) # Chave estrangeira para o usuário

    def __repr__(self):
        return f"<Bill {self.description} - {self.dueDate} - {self.status}>"

# --- Funções de Lógica de Negócios (Interagem com o Banco de Dados e FILTRAM POR USUÁRIO) ---

def add_transaction_db(description, amount, date, type, user_id): # Adicionado user_id
    new_transaction = Transaction(
        description=description,
        amount=float(amount),
        date=date,
        type=type,
        user_id=user_id # Salva o user_id
    )
    db.session.add(new_transaction)
    db.session.commit()

def add_bill_db(description, amount, due_date, user_id): # Adicionado user_id
    new_bill = Bill(
        description=description,
        amount=float(amount),
        dueDate=due_date,
        status='pending',
        user_id=user_id # Salva o user_id
    )
    db.session.add(new_bill)
    db.session.commit()

def pay_bill_db(bill_id, user_id): # Adicionado user_id para segurança
    # Garante que o usuário só possa pagar suas próprias contas
    bill = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if bill:
        bill.status = 'paid'
        db.session.add(bill)
        
        add_transaction_db(
            f"Pagamento: {bill.description}",
            bill.amount,
            datetime.date.today().isoformat(),
            'expense',
            user_id # Passa o user_id para a transação gerada pelo pagamento
        )
        db.session.commit()
        return True
    return False

def reschedule_bill_db(bill_id, new_date, user_id): # Adicionado user_id para segurança
    bill = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if bill:
        bill.dueDate = new_date
        db.session.add(bill)
        db.session.commit()
        return True
    return False

def delete_transaction_db(transaction_id, user_id): # Adicionado user_id para segurança
    transaction = Transaction.query.filter_by(id=transaction_id, user_id=user_id).first()
    if transaction:
        db.session.delete(transaction)
        db.session.commit()
        return True
    return False

def delete_bill_db(bill_id, user_id): # Adicionado user_id para segurança
    bill = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if bill:
        db.session.delete(bill)
        db.session.commit()
        return True
    return False

def get_dashboard_data_db(user_id): # Agora recebe user_id
    # Filtra todas as transações e contas pelo user_id
    all_transactions = Transaction.query.filter_by(user_id=user_id).all()
    all_bills = Bill.query.filter_by(user_id=user_id).all()

    total_income = sum(t.amount for t in all_transactions if t.type == 'income')
    total_expenses = sum(t.amount for t in all_transactions if t.type == 'expense')
    balance = total_income - total_expenses
    
    pending_bills = [b for b in all_bills if b.status == 'pending']
    total_pending_bills_amount = sum(b.amount for b in pending_bills)
    
    return {
        'balance': balance,
        'totalIncome': total_income,
        'totalExpenses': total_expenses,
        'totalPendingBills': total_pending_bills_amount,
        'pendingBillsList': pending_bills
    }

# --- Rotas da Aplicação ---

@app.route('/')
@login_required # Esta rota agora exige que o usuário esteja logado
def index():
    # Passa current_user.id para as funções de busca de dados
    dashboard_data = get_dashboard_data_db(current_user.id)
    all_transactions = Transaction.query.filter_by(user_id=current_user.id).all()
    income_transactions = [t for t in all_transactions if t.type == 'income']
    expense_transactions = [t for t in all_transactions if t.type == 'expense']

    return render_template(
        'index.html',
        dashboard=dashboard_data,
        transactions=all_transactions,
        bills=dashboard_data['pendingBillsList'],
        income_transactions=income_transactions,
        expense_transactions=expense_transactions,
        current_date=datetime.date.today().isoformat(),
        current_user=current_user # Passa o objeto current_user para o template
    )

@app.route('/add_transaction', methods=['POST'])
@login_required # Protege a rota
def handle_add_transaction():
    description = request.form['description']
    amount = request.form['amount']
    date = request.form['date']
    transaction_type = request.form['type']
    
    add_transaction_db(description, amount, date, transaction_type, current_user.id) # Passa user_id
    return redirect(url_for('index'))

@app.route('/add_bill', methods=['POST'])
@login_required # Protege a rota
def handle_add_bill():
    description = request.form['bill_description']
    amount = request.form['bill_amount']
    due_date = request.form['bill_due_date']
    
    add_bill_db(description, amount, due_date, current_user.id) # Passa user_id
    return redirect(url_for('index'))

@app.route('/pay_bill/<int:bill_id>', methods=['POST'])
@login_required # Protege a rota
def handle_pay_bill(bill_id):
    pay_bill_db(bill_id, current_user.id) # Passa user_id
    return redirect(url_for('index'))

@app.route('/reschedule_bill/<int:bill_id>', methods=['POST'])
@login_required # Protege a rota
def handle_reschedule_bill(bill_id):
    new_date = request.form['new_date']
    reschedule_bill_db(bill_id, new_date, current_user.id) # Passa user_id
    return redirect(url_for('index'))

@app.route('/delete_transaction/<int:transaction_id>', methods=['POST'])
@login_required # Protege a rota
def handle_delete_transaction(transaction_id):
    delete_transaction_db(transaction_id, current_user.id) # Passa user_id
    return redirect(url_for('index'))

@app.route('/delete_bill/<int:bill_id>', methods=['POST'])
@login_required # Protege a rota
def handle_delete_bill(bill_id):
    delete_bill_db(bill_id, current_user.id) # Passa user_id
    return redirect(url_for('index'))

# --- NOVAS ROTAS: Registro e Login ---

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        # Verifica se o usuário já existe
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash('Nome de usuário já existe. Por favor, escolha outro.', 'danger')
        else:
            new_user = User(username=username)
            new_user.set_password(password) # Hash da senha
            db.session.add(new_user)
            db.session.commit()
            flash('Conta criada com sucesso! Faça login.', 'success')
            return redirect(url_for('login'))
    return render_template('register.html') # Você precisará criar este template

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: # Se o usuário já estiver logado, redireciona para a página principal
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password): # Verifica a senha
            login_user(user) # Loga o usuário
            flash('Login realizado com sucesso!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Nome de usuário ou senha incorretos.', 'danger')
    return render_template('login.html') # Você precisará criar este template

@app.route('/logout')
@login_required # Só permite logout se já estiver logado
def logout():
    logout_user() # Desloga o usuário
    flash('Você foi desconectado.', 'info')
    return redirect(url_for('login'))


if __name__ == '__main__':
    with app.app_context():
        db.create_all() # Cria as tabelas User, Transaction, Bill
        
        # Opcional: Criar um usuário admin na primeira execução se não existir
        if not User.query.filter_by(username='admin').first():
            admin_user = User(username='admin')
            admin_user.set_password('admin123') # Senha para o usuário admin (MUDAR EM PRODUÇÃO!)
            db.session.add(admin_user)
            db.session.commit()
            print("Usuário 'admin' criado com senha 'admin123'.") # Apenas para o console local

        # Dados iniciais para contas a pagar (apenas se não houver NENHUMA conta,
        # e agora associados ao primeiro usuário criado, se houver)
        if not Bill.query.first() and User.query.first(): # Adicionado verificação de user
            first_user = User.query.first()
            db.session.add(Bill(description='Aluguel', amount=1200.00, dueDate='2025-07-25', status='pending', user_id=first_user.id))
            db.session.add(Bill(description='Energia Elétrica', amount=280.50, dueDate='2025-07-20', status='pending', user_id=first_user.id))
            db.session.add(Bill(description='Internet', amount=99.90, dueDate='2025-07-15', status='pending', user_id=first_user.id))
            db.session.commit()

    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
