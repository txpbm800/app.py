# app.py

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import datetime
import os
import google.generativeai as genai

app = Flask(__name__)

# --- CONFIGURAÇÃO DA API KEY GEMINI (mantido) ---
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', 'SUA_CHAVE_DE_API_GEMINI_AQUI') # Substitua pela sua chave real
genai.configure(api_key=GEMINI_API_KEY)

# Configuração da Chave Secreta para Flask-Login e Flask-WTF
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'uma_chave_secreta_muito_complexa_e_aleatoria') # Use uma variável de ambiente em produção!

# Configuração do Banco de Dados SQLite
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'finance.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# --- Definição dos Modelos do Banco de Dados (ORM) ---

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    profile_picture_url = db.Column(db.String(255), nullable=True, default='https://placehold.co/100x100/aabbcc/ffffff?text=PF')
    
    transactions = db.relationship('Transaction', backref='user', lazy=True, cascade='all, delete-orphan')
    bills = db.relationship('Bill', backref='user', lazy=True, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.username}>"

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.String(10), nullable=False)
    type = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    def __repr__(self):
        return f"<Transaction {self.description} - {self.amount}>"

class Bill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    dueDate = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    def __repr__(self):
        return f"<Bill {self.description} - {self.dueDate} - {self.status}>"

# --- Funções de Lógica de Negócios ---

def add_transaction_db(description, amount, date, type, user_id):
    new_transaction = Transaction(
        description=description,
        amount=float(amount),
        date=date,
        type=type,
        user_id=user_id
    )
    db.session.add(new_transaction)
    db.session.commit()

def add_bill_db(description, amount, due_date, user_id):
    new_bill = Bill(
        description=description,
        amount=float(amount),
        dueDate=due_date,
        status='pending',
        user_id=user_id
    )
    db.session.add(new_bill)
    db.session.commit()

def pay_bill_db(bill_id, user_id):
    bill = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if bill:
        bill.status = 'paid'
        db.session.add(bill)
        
        add_transaction_db(
            f"Pagamento: {bill.description}",
            bill.amount,
            datetime.date.today().isoformat(),
            'expense',
            user_id
        )
        db.session.commit()
        return True
    return False

def reschedule_bill_db(bill_id, new_date, user_id):
    bill = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if bill:
        bill.dueDate = new_date
        db.session.add(bill)
        db.session.commit()
        return True
    return False

def delete_transaction_db(transaction_id, user_id):
    transaction = Transaction.query.filter_by(id=transaction_id, user_id=user_id).first()
    if transaction:
        db.session.delete(transaction)
        db.session.commit()
        return True
    return False

def delete_bill_db(bill_id, user_id):
    bill = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if bill:
        db.session.delete(bill)
        db.session.commit()
        return True
    return False

def edit_transaction_db(transaction_id, description, amount, date, type, user_id):
    transaction = Transaction.query.filter_by(id=transaction_id, user_id=user_id).first()
    if transaction:
        transaction.description = description
        transaction.amount = float(amount)
        transaction.date = date
        transaction.type = type
        db.session.commit()
        return True
    return False

def edit_bill_db(bill_id, description, amount, dueDate, user_id):
    bill = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if bill:
        bill.description = description
        bill.amount = float(amount)
        bill.dueDate = dueDate
        db.session.commit()
        return True
    return False

def get_dashboard_data_db(user_id):
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

def generate_text_with_gemini(prompt_text):
    try:
        model = genai.GenerativeModel('gemini-2.0-flash')
        response = model.generate_content(prompt_text)
        return response.text
    except Exception as e:
        print(f"Erro ao chamar Gemini API: {e}")
        return "Não foi possível gerar uma sugestão/resumo no momento."

# --- Rotas da Aplicação (ATUALIZADAS COM FILTROS E ORDENAÇÃO) ---

@app.route('/')
@login_required
def index():
    dashboard_data = get_dashboard_data_db(current_user.id)
    
    # --- FILTRAGEM E ORDENAÇÃO PARA TRANSAÇÕES ---
    transactions_query = Transaction.query.filter_by(user_id=current_user.id)

    # Filtro por tipo de transação
    transaction_type_filter = request.args.get('transaction_type')
    if transaction_type_filter and transaction_type_filter in ['income', 'expense']:
        transactions_query = transactions_query.filter_by(type=transaction_type_filter)

    # Filtro por período de data
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    if start_date:
        transactions_query = transactions_query.filter(Transaction.date >= start_date)
    if end_date:
        transactions_query = transactions_query.filter(Transaction.date <= end_date)

    # Ordenação de transações
    sort_by_transactions = request.args.get('sort_by_transactions', 'date') # Padrão: por data
    order_transactions = request.args.get('order_transactions', 'desc') # Padrão: decrescente

    if sort_by_transactions == 'date':
        if order_transactions == 'asc':
            transactions_query = transactions_query.order_by(Transaction.date.asc())
        else:
            transactions_query = transactions_query.order_by(Transaction.date.desc())
    elif sort_by_transactions == 'amount':
        if order_transactions == 'asc':
            transactions_query = transactions_query.order_by(Transaction.amount.asc())
        else:
            transactions_query = transactions_query.order_by(Transaction.amount.desc())
    
    all_transactions = transactions_query.all()
    
    # Prepara as listas filtradas para as seções específicas de Receitas e Despesas
    income_transactions = [t for t in all_transactions if t.type == 'income']
    expense_transactions = [t for t in all_transactions if t.type == 'expense']


    # --- FILTRAGEM E ORDENAÇÃO PARA CONTAS (BILLS) ---
    bills_query = Bill.query.filter_by(user_id=current_user.id)

    # Filtro por status da conta
    bill_status_filter = request.args.get('bill_status')
    if bill_status_filter and bill_status_filter in ['pending', 'paid', 'overdue']:
        if bill_status_filter == 'overdue':
            today_str = datetime.date.today().isoformat()
            bills_query = bills_query.filter(Bill.dueDate < today_str, Bill.status == 'pending')
        else:
            bills_query = bills_query.filter_by(status=bill_status_filter)
    elif not bill_status_filter: # Se nenhum filtro de status, mostrar apenas pendentes por padrão para a bills list principal
         bills_query = bills_query.filter_by(status='pending')


    # Ordenação de contas
    sort_by_bills = request.args.get('sort_by_bills', 'dueDate') # Padrão: por data de vencimento
    order_bills = request.args.get('order_bills', 'asc') # Padrão: crescente (vencimentos mais próximos primeiro)

    if sort_by_bills == 'dueDate':
        if order_bills == 'asc':
            bills_query = bills_query.order_by(Bill.dueDate.asc())
        else:
            bills_query = bills_query.order_by(Bill.dueDate.desc())
    elif sort_by_bills == 'amount':
        if order_bills == 'asc':
            bills_query = bills_query.order_by(Bill.amount.asc())
        else:
            bills_query = bills_query.order_by(Bill.amount.desc())
            
    filtered_bills = bills_query.all() # Isso agora será o bills que passamos para a BillsList

    return render_template(
        'index.html',
        dashboard=dashboard_data,
        transactions=all_transactions, # Transações já filtradas/ordenadas
        bills=filtered_bills, # Contas já filtradas/ordenadas
        income_transactions=income_transactions, # Receitas filtradas por data/ordenadas
        expense_transactions=expense_transactions, # Despesas filtradas por data/ordenadas
        current_date=datetime.date.today().isoformat(),
        current_user=current_user,
        # Passa os valores de filtro/ordenação atuais para preencher os controles no frontend
        current_transaction_type_filter=transaction_type_filter,
        current_bill_status_filter=bill_status_filter,
        current_sort_by_transactions=sort_by_transactions,
        current_order_transactions=order_transactions,
        current_sort_by_bills=sort_by_bills,
        current_order_bills=order_bills,
        current_start_date=start_date,
        current_end_date=end_date
    )

@app.route('/add_transaction', methods=['POST'])
@login_required
def handle_add_transaction():
    description = request.form['description']
    amount = request.form['amount']
    date = request.form['date']
    transaction_type = request.form['type']
    
    add_transaction_db(description, amount, date, transaction_type, current_user.id)
    flash('Transação adicionada com sucesso!', 'success')
    return redirect(url_for('index'))

@app.route('/add_bill', methods=['POST'])
@login_required
def handle_add_bill():
    description = request.form['bill_description']
    amount = request.form['bill_amount']
    due_date = request.form['bill_due_date']
    
    add_bill_db(description, amount, due_date, current_user.id)
    flash('Conta adicionada com sucesso!', 'success')
    return redirect(url_for('index'))

@app.route('/pay_bill/<int:bill_id>', methods=['POST'])
@login_required
def handle_pay_bill(bill_id):
    if pay_bill_db(bill_id, current_user.id):
        flash('Conta paga e transação registrada com sucesso!', 'success')
    else:
        flash('Não foi possível pagar a conta. Verifique se ela existe ou pertence a você.', 'danger')
    return redirect(url_for('index'))

@app.route('/reschedule_bill/<int:bill_id>', methods=['POST'])
@login_required
def handle_reschedule_bill(bill_id):
    new_date = request.form['new_date']
    if reschedule_bill_db(bill_id, new_date, current_user.id):
        flash('Conta remarcada com sucesso!', 'success')
    else:
        flash('Não foi possível remarcar a conta. Verifique se ela existe ou pertence a você.', 'danger')
    return redirect(url_for('index'))

@app.route('/delete_transaction/<int:transaction_id>', methods=['POST'])
@login_required
def handle_delete_transaction(transaction_id):
    if delete_transaction_db(transaction_id, current_user.id):
        flash('Transação excluída com sucesso!', 'info')
    else:
        flash('Não foi possível excluir a transação. Verifique se ela existe ou pertence a você.', 'danger')
    return redirect(url_for('index'))

@app.route('/delete_bill/<int:bill_id>', methods=['POST'])
@login_required
def handle_delete_bill(bill_id):
    if delete_bill_db(bill_id, current_user.id):
        flash('Conta excluída com sucesso!', 'info')
    else:
        flash('Não foi possível excluir a conta. Verifique se ela existe ou pertence a você.', 'danger')
    return redirect(url_for('index'))

@app.route('/get_transaction_data/<int:transaction_id>', methods=['GET'])
@login_required
def get_transaction_data(transaction_id):
    transaction = Transaction.query.filter_by(id=transaction_id, user_id=current_user.id).first()
    if transaction:
        return jsonify({
            'id': transaction.id,
            'description': transaction.description,
            'amount': transaction.amount,
            'date': transaction.date,
            'type': transaction.type
        })
    return jsonify({'error': 'Transação não encontrada ou não pertence a este usuário'}), 404

@app.route('/edit_transaction/<int:transaction_id>', methods=['POST'])
@login_required
def handle_edit_transaction(transaction_id):
    description = request.form['edit_description']
    amount = request.form['edit_amount']
    date = request.form['edit_date']
    transaction_type = request.form['edit_type']

    if edit_transaction_db(transaction_id, description, amount, date, transaction_type, current_user.id):
        flash('Transação atualizada com sucesso!', 'success')
    else:
        flash('Não foi possível atualizar a transação. Verifique se ela existe ou pertence a você.', 'danger')
    return redirect(url_for('index'))

@app.route('/get_bill_data/<int:bill_id>', methods=['GET'])
@login_required
def get_bill_data(bill_id):
    bill = Bill.query.filter_by(id=bill_id, user_id=current_user.id).first()
    if bill:
        return jsonify({
            'id': bill.id,
            'description': bill.description,
            'amount': bill.amount,
            'dueDate': bill.dueDate,
            'status': bill.status
        })
    return jsonify({'error': 'Conta não encontrada ou não pertence a este usuário'}), 404

@app.route('/edit_bill/<int:bill_id>', methods=['POST'])
@login_required
def handle_edit_bill(bill_id):
    description = request.form['edit_bill_description']
    amount = request.form['edit_bill_amount']
    due_date = request.form['edit_bill_dueDate']

    if edit_bill_db(bill_id, description, amount, due_date, current_user.id):
        flash('Conta atualizada com sucesso!', 'success')
    else:
        flash('Não foi possível atualizar a conta. Verifique se ela existe ou pertence a você.', 'danger')
    return redirect(url_for('index'))


# --- ROTAS DE AUTENTICAÇÃO E GERENCIAMENTO DE USUÁRIO (mantidas) ---

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash('Nome de usuário já existe. Por favor, escolha outro.', 'danger')
        else:
            new_user = User(username=username)
            new_user.set_password(password)
            db.session.add(new_user)
            db.session.commit()
            flash('Conta criada com sucesso! Faça login.', 'success')
            return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            flash('Login realizado com sucesso!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Nome de usuário ou senha incorretos.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Você foi desconectado.', 'info')
    return redirect(url_for('login'))

@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html', current_user=current_user)

@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        old_password = request.form['old_password']
        new_password = request.form['new_password']
        confirm_new_password = request.form['confirm_new_password']

        if not current_user.check_password(old_password):
            flash('Senha antiga incorreta.', 'danger')
        elif new_password != confirm_new_password:
            flash('A nova senha e a confirmação não coincidem.', 'danger')
        else:
            current_user.set_password(new_password)
            db.session.commit()
            flash('Senha alterada com sucesso!', 'success')
            return redirect(url_for('profile'))
    return render_template('change_password.html')

@app.route('/delete_account', methods=['GET', 'POST'])
@login_required
def delete_account():
    if request.method == 'POST':
        confirm_password = request.form['confirm_password']

        if current_user.check_password(confirm_password):
            user_to_delete = User.query.get(current_user.id)
            if user_to_delete:
                logout_user()
                db.session.delete(user_to_delete)
                db.session.commit()
                flash('Sua conta foi excluída permanentemente.', 'success')
                return redirect(url_for('register'))
            else:
                flash('Erro ao encontrar sua conta.', 'danger')
        else:
            flash('Senha incorreta.', 'danger')
    return render_template('delete_account.html')

@app.route('/profile/monthly_summary', methods=['GET'])
@login_required
def get_monthly_summary():
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)

    if not year or not month:
        current_date = datetime.date.today()
        year = current_date.year
        month = current_date.month

    monthly_transactions = [
        t for t in Transaction.query.filter_by(user_id=current_user.id).all()
        if datetime.datetime.strptime(t.date, '%Y-%m-%d').year == year and
           datetime.datetime.strptime(t.date, '%Y-%m-%d').month == month
    ]

    monthly_income = sum(t.amount for t in monthly_transactions if t.type == 'income')
    monthly_expenses = sum(t.amount for t in monthly_transactions if t.type == 'expense')
    monthly_balance = monthly_income - monthly_expenses
    
    return jsonify({
        'year': year,
        'month': month,
        'income': monthly_income,
        'expenses': monthly_expenses,
        'balance': monthly_balance,
        'transactions_details': [
            {'description': t.description, 'amount': t.amount, 'type': t.type, 'date': t.date}
            for t in monthly_transactions
        ]
    })

@app.route('/profile/ai_insight', methods=['POST'])
@login_required
def get_ai_insight():
    data = request.get_json()
    monthly_summary = data.get('summary_data')
    
    if not monthly_summary:
        return jsonify({'error': 'Dados de resumo não fornecidos.'}), 400

    prompt = (
        f"Com base nos seguintes dados financeiros de um mês específico: "
        f"Receita Total: R${monthly_summary['income']:.2f}, "
        f"Despesa Total: R${monthly_summary['expenses']:.2f}, "
        f"Saldo Mensal: R${monthly_summary['balance']:.2f}. "
        f"Detalhes das transações: {monthly_summary['transactions_details']}. "
        f"Forneça um breve insight ou conselho financeiro pessoal para o usuário. "
        f"Concentre-se em pontos fortes, áreas para melhoria ou tendências. "
        f"Use uma linguagem amigável e direta em português. "
        f"Seja conciso, com no máximo 100 palavras. "
        f"Não inclua 'Olá!' ou saudações, vá direto ao ponto."
    )

    ai_text = generate_text_with_gemini(prompt)
    return jsonify({'insight': ai_text})

@app.route('/profile/update_picture', methods=['POST'])
@login_required
def update_profile_picture():
    picture_url = request.form['profile_picture_url']
    current_user.profile_picture_url = picture_url
    db.session.commit()
    flash('Foto de perfil atualizada com sucesso!', 'success')
    return redirect(url_for('profile'))


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        
        if not User.query.filter_by(username='admin').first():
            admin_user = User(username='admin')
            admin_user.set_password('admin123')
            db.session.add(admin_user)
            db.session.commit()
            print("Usuário 'admin' criado com senha 'admin123'.")

        if not Bill.query.first() and User.query.first():
            first_user = User.query.first()
            if first_user:
                db.session.add(Bill(description='Aluguel', amount=1200.00, dueDate='2025-07-25', status='pending', user_id=first_user.id))
                db.session.add(Bill(description='Energia Elétrica', amount=280.50, dueDate='2025-07-20', status='pending', user_id=first_user.id))
                db.session.add(Bill(description='Internet', amount=99.90, dueDate='2025-01-15', status='pending', user_id=first_user.id))
                db.session.commit()

    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
