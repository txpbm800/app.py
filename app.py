# app.py

from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
import datetime
import os # Para lidar com caminhos de arquivo e variáveis de ambiente

app = Flask(__name__)

# Configuração do Banco de Dados SQLite
# BASE_DIR é o diretório base da sua aplicação (onde app.py está)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
# Define o caminho para o arquivo do banco de dados SQLite
# O arquivo 'finance.db' será criado na raiz do seu projeto
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'finance.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False # Desativa o rastreamento de modificações para otimização

db = SQLAlchemy(app)

# --- Definição dos Modelos do Banco de Dados (ORM) ---

# Modelo para Transações
class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True) # ID único, gerado automaticamente
    description = db.Column(db.String(200), nullable=False) # Descrição da transação
    amount = db.Column(db.Float, nullable=False) # Valor da transação
    date = db.Column(db.String(10), nullable=False) # Data da transação (YYYY-MM-DD)
    type = db.Column(db.String(10), nullable=False) # Tipo: 'income' ou 'expense'

    def __repr__(self):
        return f"<Transaction {self.description} - {self.amount}>"

# Modelo para Contas a Pagar
class Bill(db.Model):
    id = db.Column(db.Integer, primary_key=True) # ID único, gerado automaticamente
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    dueDate = db.Column(db.String(10), nullable=False) # Data de vencimento (YYYY-MM-DD)
    status = db.Column(db.String(10), nullable=False) # Status: 'pending' ou 'paid'

    def __repr__(self):
        return f"<Bill {self.description} - {self.dueDate} - {self.status}>"

# --- Funções de Lógica de Negócios (Interagem com o Banco de Dados) ---

# Função para inicializar o banco de dados e criar tabelas
# Isso será chamado antes da primeira requisição
@app.before_first_request
def create_tables():
    db.create_all()
    # Adiciona dados iniciais se o banco de dados estiver vazio
    if not Bill.query.first():
        db.session.add(Bill(description='Aluguel', amount=1200.00, dueDate='2025-07-25', status='pending'))
        db.session.add(Bill(description='Energia Elétrica', amount=280.50, dueDate='2025-07-20', status='pending'))
        db.session.add(Bill(description='Internet', amount=99.90, dueDate='2025-07-15', status='pending'))
        db.session.commit()

def add_transaction_db(description, amount, date, type):
    """Adiciona uma nova transação ao banco de dados."""
    new_transaction = Transaction(
        description=description,
        amount=float(amount),
        date=date,
        type=type
    )
    db.session.add(new_transaction)
    db.session.commit()

def add_bill_db(description, amount, due_date):
    """Adiciona uma nova conta a pagar ao banco de dados."""
    new_bill = Bill(
        description=description,
        amount=float(amount),
        dueDate=due_date,
        status='pending'
    )
    db.session.add(new_bill)
    db.session.commit()

def pay_bill_db(bill_id):
    """Marca uma conta como paga e adiciona uma transação de despesa."""
    bill = Bill.query.get(bill_id) # Busca a conta pelo ID
    if bill:
        bill.status = 'paid' # Atualiza o status
        db.session.add(bill) # Adiciona a alteração ao session
        
        # Adiciona a transação de despesa
        add_transaction_db(
            f"Pagamento: {bill.description}",
            bill.amount,
            datetime.date.today().isoformat(), # Data atual
            'expense'
        )
        db.session.commit() # Salva todas as alterações no banco
        return True
    return False

def reschedule_bill_db(bill_id, new_date):
    """Remarca a data de vencimento de uma conta."""
    bill = Bill.query.get(bill_id)
    if bill:
        bill.dueDate = new_date
        db.session.add(bill)
        db.session.commit()
        return True
    return False

def get_dashboard_data_db():
    """Calcula e retorna os dados para o dashboard do banco de dados."""
    all_transactions = Transaction.query.all()
    all_bills = Bill.query.all()

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
def index():
    """Rota principal que exibe o dashboard e os formulários/listas."""
    dashboard_data = get_dashboard_data_db()
    
    # Busca todas as transações e as filtra para exibição
    all_transactions = Transaction.query.all()
    income_transactions = [t for t in all_transactions if t.type == 'income']
    expense_transactions = [t for t in all_transactions if t.type == 'expense']

    return render_template(
        'index.html',
        dashboard=dashboard_data,
        transactions=all_transactions, # Todas as transações
        bills=dashboard_data['pendingBillsList'], # Contas pendentes
        income_transactions=income_transactions,
        expense_transactions=expense_transactions,
        current_date=datetime.date.today().isoformat()
    )

@app.route('/add_transaction', methods=['POST'])
def handle_add_transaction():
    """Rota para adicionar uma nova transação."""
    description = request.form['description']
    amount = request.form['amount']
    date = request.form['date']
    transaction_type = request.form['type']
    
    add_transaction_db(description, amount, date, transaction_type)
    return redirect(url_for('index'))

@app.route('/add_bill', methods=['POST'])
def handle_add_bill():
    """Rota para adicionar uma nova conta a pagar."""
    description = request.form['bill_description']
    amount = request.form['bill_amount']
    due_date = request.form['bill_due_date']
    
    add_bill_db(description, amount, due_date)
    return redirect(url_for('index'))

@app.route('/pay_bill/<int:bill_id>', methods=['POST'])
def handle_pay_bill(bill_id):
    """Rota para pagar uma conta."""
    pay_bill_db(bill_id)
    return redirect(url_for('index'))

@app.route('/reschedule_bill/<int:bill_id>', methods=['POST'])
def handle_reschedule_bill(bill_id):
    """Rota para remarcar uma conta."""
    new_date = request.form['new_date']
    reschedule_bill_db(bill_id, new_date)
    return redirect(url_for('index'))

if __name__ == '__main__':
    # Obtém a porta do ambiente (fornecida pelo Render ou padrão 5000 para local)
    port = int(os.environ.get('PORT', 5000))
    # Inicia o Flask na porta e host que o Render espera (0.0.0.0 para ser acessível externamente)
    app.run(host='0.0.0.0', port=port, debug=False) # Mude debug para True para desenvolvimento local
