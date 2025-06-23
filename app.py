# app.py

from flask import Flask, render_template, request, redirect, url_for
import datetime
import uuid # Para gerar IDs únicos para transações e contas

app = Flask(__name__)

# Simulação de um banco de dados em memória
# Em uma aplicação real, você usaria um banco de dados como SQLite, PostgreSQL, etc.
transactions = []
bills = [
    {
        'id': str(uuid.uuid4()),
        'description': 'Aluguel',
        'amount': 1200.00,
        'dueDate': '2025-07-25', # Ajustado para uma data futura para teste
        'status': 'pending'
    },
    {
        'id': str(uuid.uuid4()),
        'description': 'Energia Elétrica',
        'amount': 280.50,
        'dueDate': '2025-07-20', # Ajustado para uma data futura para teste
        'status': 'pending'
    },
    {
        'id': str(uuid.uuid4()),
        'description': 'Internet',
        'amount': 99.90,
        'dueDate': '2025-07-15', # Ajustado para uma data futura para teste
        'status': 'pending'
    }
]

# --- Funções de Lógica de Negócios (Semelhante ao useFinancialData do React) ---

def add_transaction(description, amount, date, type):
    """Adiciona uma nova transação."""
    new_transaction = {
        'id': str(uuid.uuid4()),
        'description': description,
        'amount': float(amount),
        'date': date,
        'type': type
    }
    transactions.append(new_transaction)

def add_bill(description, amount, due_date):
    """Adiciona uma nova conta a pagar."""
    new_bill = {
        'id': str(uuid.uuid4()),
        'description': description,
        'amount': float(amount),
        'dueDate': due_date,
        'status': 'pending'
    }
    bills.append(new_bill)

def pay_bill(bill_id):
    """Marca uma conta como paga e adiciona uma transação de despesa."""
    for bill in bills:
        if bill['id'] == bill_id:
            bill['status'] = 'paid'
            add_transaction(
                f"Pagamento: {bill['description']}",
                bill['amount'],
                datetime.date.today().isoformat(), # Data atual
                'expense'
            )
            return True
    return False

def reschedule_bill(bill_id, new_date):
    """Remarca a data de vencimento de uma conta."""
    for bill in bills:
        if bill['id'] == bill_id:
            bill['dueDate'] = new_date
            return True
    return False

def get_dashboard_data():
    """Calcula e retorna os dados para o dashboard."""
    total_income = sum(t['amount'] for t in transactions if t['type'] == 'income')
    total_expenses = sum(t['amount'] for t in transactions if t['type'] == 'expense')
    balance = total_income - total_expenses
    
    pending_bills = [b for b in bills if b['status'] == 'pending']
    total_pending_bills_amount = sum(b['amount'] for b in pending_bills)
    
    return {
        'balance': balance,
        'totalIncome': total_income,
        'totalExpenses': total_expenses,
        'totalPendingBills': total_pending_bills_amount,
        'pendingBillsList': pending_bills # Retorna a lista de contas pendentes para a BillsList
    }

# --- Rotas da Aplicação ---

@app.route('/')
def index():
    """Rota principal que exibe o dashboard e os formulários/listas."""
    dashboard_data = get_dashboard_data()
    
    # Filtra transações por tipo para exibição nas listas
    income_transactions = [t for t in transactions if t['type'] == 'income']
    expense_transactions = [t for t in transactions if t['type'] == 'expense']

    return render_template(
        'index.html',
        dashboard=dashboard_data,
        transactions=transactions,
        bills=dashboard_data['pendingBillsList'], # Passa a lista de contas pendentes
        income_transactions=income_transactions,
        expense_transactions=expense_transactions,
        current_date=datetime.date.today().isoformat() # Passa a data atual para preencher o formulário
    )

@app.route('/add_transaction', methods=['POST'])
def handle_add_transaction():
    """Rota para adicionar uma nova transação."""
    description = request.form['description']
    amount = request.form['amount']
    date = request.form['date']
    transaction_type = request.form['type']
    
    add_transaction(description, amount, date, transaction_type)
    return redirect(url_for('index'))

@app.route('/add_bill', methods=['POST'])
def handle_add_bill():
    """Rota para adicionar uma nova conta a pagar."""
    description = request.form['bill_description']
    amount = request.form['bill_amount']
    due_date = request.form['bill_due_date']
    
    add_bill(description, amount, due_date)
    return redirect(url_for('index'))

@app.route('/pay_bill/<bill_id>', methods=['POST'])
def handle_pay_bill(bill_id):
    """Rota para pagar uma conta."""
    pay_bill(bill_id)
    return redirect(url_for('index'))

@app.route('/reschedule_bill/<bill_id>', methods=['POST'])
def handle_reschedule_bill(bill_id):
    """Rota para remarcar uma conta."""
    new_date = request.form['new_date']
    reschedule_bill(bill_id, new_date)
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True) # debug=True para desenvolvimento (recarrega ao salvar mudanças)
