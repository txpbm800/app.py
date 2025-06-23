# app.py

from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
import datetime
import os

app = Flask(__name__)

# Configuração do Banco de Dados SQLite
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'finance.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- Definição dos Modelos do Banco de Dados (ORM) ---

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.String(10), nullable=False)
    type = db.Column(db.String(10), nullable=False)

    def __repr__(self):
        return f"<Transaction {self.description} - {self.amount}>"

class Bill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    dueDate = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(10), nullable=False)

    def __repr__(self):
        return f"<Bill {self.description} - {self.dueDate} - {self.status}>"

# --- Funções de Lógica de Negócios (Interagem com o Banco de Dados) ---

def add_transaction_db(description, amount, date, type):
    new_transaction = Transaction(
        description=description,
        amount=float(amount),
        date=date,
        type=type
    )
    db.session.add(new_transaction)
    db.session.commit()

def add_bill_db(description, amount, due_date):
    new_bill = Bill(
        description=description,
        amount=float(amount),
        dueDate=due_date,
        status='pending'
    )
    db.session.add(new_bill)
    db.session.commit()

def pay_bill_db(bill_id):
    bill = Bill.query.get(bill_id)
    if bill:
        bill.status = 'paid'
        db.session.add(bill)
        
        add_transaction_db(
            f"Pagamento: {bill.description}",
            bill.amount,
            datetime.date.today().isoformat(),
            'expense'
        )
        db.session.commit()
        return True
    return False

def reschedule_bill_db(bill_id, new_date):
    bill = Bill.query.get(bill_id)
    if bill:
        bill.dueDate = new_date
        db.session.add(bill)
        db.session.commit()
        return True
    return False

# NOVA FUNÇÃO: Excluir Transação
def delete_transaction_db(transaction_id):
    transaction = Transaction.query.get(transaction_id)
    if transaction:
        db.session.delete(transaction)
        db.session.commit()
        return True
    return False

# NOVA FUNÇÃO: Excluir Conta a Pagar
def delete_bill_db(bill_id):
    bill = Bill.query.get(bill_id)
    if bill:
        db.session.delete(bill)
        db.session.commit()
        return True
    return False

def get_dashboard_data_db():
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
    dashboard_data = get_dashboard_data_db()
    all_transactions = Transaction.query.all()
    income_transactions = [t for t in all_transactions if t.type == 'income']
    expense_transactions = [t for t in all_transactions if t.type == 'expense']

    return render_template(
        'index.html',
        dashboard=dashboard_data,
        transactions=all_transactions,
        bills=dashboard_data['pendingBillsList'],
        income_transactions=income_transactions,
        expense_transactions=expense_transactions,
        current_date=datetime.date.today().isoformat()
    )

@app.route('/add_transaction', methods=['POST'])
def handle_add_transaction():
    description = request.form['description']
    amount = request.form['amount']
    date = request.form['date']
    transaction_type = request.form['type']
    
    add_transaction_db(description, amount, date, transaction_type)
    return redirect(url_for('index'))

@app.route('/add_bill', methods=['POST'])
def handle_add_bill():
    description = request.form['bill_description']
    amount = request.form['bill_amount']
    due_date = request.form['bill_due_date']
    
    add_bill_db(description, amount, due_date)
    return redirect(url_for('index'))

@app.route('/pay_bill/<int:bill_id>', methods=['POST'])
def handle_pay_bill(bill_id):
    pay_bill_db(bill_id)
    return redirect(url_for('index'))

@app.route('/reschedule_bill/<int:bill_id>', methods=['POST'])
def handle_reschedule_bill(bill_id):
    new_date = request.form['new_date']
    reschedule_bill_db(bill_id, new_date)
    return redirect(url_for('index'))

# NOVA ROTA: Excluir Transação
@app.route('/delete_transaction/<int:transaction_id>', methods=['POST'])
def handle_delete_transaction(transaction_id):
    delete_transaction_db(transaction_id)
    return redirect(url_for('index'))

# NOVA ROTA: Excluir Conta a Pagar
@app.route('/delete_bill/<int:bill_id>', methods=['POST'])
def handle_delete_bill(bill_id):
    delete_bill_db(bill_id)
    return redirect(url_for('index'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not Bill.query.first():
            db.session.add(Bill(description='Aluguel', amount=1200.00, dueDate='2025-07-25', status='pending'))
            db.session.add(Bill(description='Energia Elétrica', amount=280.50, dueDate='2025-07-20', status='pending'))
            db.session.add(Bill(description='Internet', amount=99.90, dueDate='2025-07-15', status='pending'))
            db.session.commit()

    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
