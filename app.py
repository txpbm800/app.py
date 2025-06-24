# app.py

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import datetime
import os
from dateutil.relativedelta import relativedelta # Para cálculo de datas recorrentes
import google.generativeai as genai

app = Flask(__name__)

# --- CONFIGURAÇÃO DA API KEY GEMINI (mantido) ---
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', 'SUA_CHAVE_DE_API_GEMINI_AQUI')
genai.configure(api_key=GEMINI_API_KEY)

# Configuração da Chave Secreta para Flask-Login e Flask-WTF
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'uma_chave_secreta_muito_complexa_e_aleatoria')

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
    # REMOVIDO: recurring_transactions relacionamento direto, pois Bills agora gerenciam isso

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.username}>"

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    type = db.Column(db.String(10), nullable=False) # 'income' ou 'expense'
    
    transactions = db.relationship('Transaction', backref='category', lazy=True)
    # REMOVIDO: recurring_transactions relacionamento direto

    def __repr__(self):
        return f"<Category {self.name} ({self.type})>"

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.String(10), nullable=False)
    type = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)

    def __repr__(self):
        return f"<Transaction {self.description} - {self.amount}>"

# MODELO BILL MODIFICADO para lidar com recorrência e parcelamento
class Bill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    dueDate = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # NOVOS CAMPOS PARA RECORRÊNCIA E PARCELAMENTO (AGORA NA PRÓPRIA BILL)
    is_recurring = db.Column(db.Boolean, default=False, nullable=False) # É uma conta recorrente?
    recurring_frequency = db.Column(db.String(20), nullable=True) # 'monthly', 'weekly', 'yearly', 'installments'
    recurring_start_date = db.Column(db.String(10), nullable=True) # Data de início da recorrência (original)
    recurring_next_due_date = db.Column(db.String(10), nullable=True) # Próxima data de vencimento a ser gerada
    recurring_installments_total = db.Column(db.Integer, nullable=True) # Total de parcelas (para frequência 'installments')
    recurring_installments_generated = db.Column(db.Integer, nullable=True, default=0) # Quantas parcelas já foram geradas/pagas
    is_active_recurring = db.Column(db.Boolean, default=False, nullable=False) # A recorrência ainda está ativa para gerar mais?


    def __repr__(self):
        return f"<Bill {self.description} - {self.dueDate} - {self.status}>"

# REMOVIDO: Modelo RecurringTransaction


# --- Funções de Lógica de Negócios ---

def add_transaction_db(description, amount, date, type, user_id, category_id=None):
    new_transaction = Transaction(
        description=description,
        amount=float(amount),
        date=date,
        type=type,
        user_id=user_id,
        category_id=category_id
    )
    db.session.add(new_transaction)
    db.session.commit()

# MODIFICADA: add_bill_db agora pode criar Bills recorrentes
def add_bill_db(description, amount, due_date, user_id, 
                is_recurring=False, recurring_frequency=None, recurring_installments_total=0):
    
    # Validação para parcelas
    if is_recurring and recurring_frequency == 'installments' and (recurring_installments_total is None or recurring_installments_total < 1):
        recurring_installments_total = 1 # Pelo menos 1 parcela se for parcelado
    elif not is_recurring or recurring_frequency != 'installments':
        recurring_installments_total = 0 # Não parcelado

    new_bill = Bill(
        description=description,
        amount=float(amount),
        dueDate=due_date,
        status='pending',
        user_id=user_id,
        is_recurring=is_recurring,
        recurring_frequency=recurring_frequency if is_recurring else None,
        recurring_start_date=due_date if is_recurring else None, # Data de início é a primeira due_date
        recurring_next_due_date=due_date if is_recurring else None, # Próxima a ser gerada é a primeira due_date
        recurring_installments_total=recurring_installments_total,
        recurring_installments_generated=0, # Inicia com 0 geradas
        is_active_recurring=is_recurring # Ativa se for recorrente
    )
    db.session.add(new_bill)
    db.session.commit() # Comita imediatamente para obter o ID da Bill, se necessário


# REMOVIDO: get_recurring_transactions_db
# REMOVIDO: edit_recurring_transaction_db
# REMOVIDO: delete_recurring_transaction_db


# REVISADA E OTIMIZADA: Processa Bills Recorrentes e Gera Próximas Ocorrências
# Esta função AGORA processa as Bills que são as "sementes" das recorrências
def process_recurring_bills(user_id):
    today = datetime.date.today()
    bills_generated_count = 0
    transactions_generated_count = 0
    
    # Buscar apenas Bills que são a "semente" da recorrência e ainda estão ativas para gerar novas ocorrências
    # As Bills que foram geradas a partir de uma recorrência terão recurring_transaction_origin_id preenchido.
    # Queremos processar APENAS as Bills ORIGINAIS que o usuário marcou como is_recurring=True
    # e que ainda estão ativas para gerar mais.
    
    recurring_seed_bills = Bill.query.filter(
        Bill.user_id == user_id,
        Bill.is_recurring == True,          # É uma Bill que marca uma recorrência
        Bill.is_active_recurring == True    # E essa recorrência ainda está ativa para gerar
    ).all()
    
    print(f"\n--- Processando {len(recurring_seed_bills)} Bills recorrentes ativas para o usuário {user_id} ---") # Debug

    for bill_seed in recurring_seed_bills:
        # Convert next_due_date from the SEED bill to datetime.date object
        next_due_date_dt = datetime.datetime.strptime(bill_seed.recurring_next_due_date, '%Y-%m-%d').date()
        
        loop_counter = 0
        MAX_GENERATIONS_PER_RUN = 36 # Safety limit to prevent infinite loops

        print(f"  Verificando Bill recorrente: '{bill_seed.description}' (ID: {bill_seed.id}), Próx. Venc.: {next_due_date_dt}, Hoje: {today}") # Debug

        # Loop to generate ALL occurrences that are due up to today
        while next_due_date_dt <= today and bill_seed.is_active_recurring and loop_counter < MAX_GENERATIONS_PER_RUN:
            print(f"  --> Data devida {next_due_date_dt.isoformat()} <= Hoje {today.isoformat()}. Gerando ocorrência...") # Debug
            
            # --- Logic for Generation ---
            
            # All recurring items of type 'expense' generate new Bill entries
            if bill_seed.type == 'expense':
                if bill_seed.recurring_frequency == 'installments':
                    if (bill_seed.recurring_installments_generated or 0) < bill_seed.recurring_installments_total:
                        installment_number_to_generate = (bill_seed.recurring_installments_generated or 0) + 1
                        
                        # Check if this specific installment Bill has already been generated
                        existing_bill_for_installment = Bill.query.filter_by(
                            recurring_transaction_origin_id=bill_seed.id, # Link to the original recurring Bill
                            dueDate=next_due_date_dt.isoformat(),
                            installment_number=installment_number_to_generate,
                            user_id=user_id
                        ).first()

                        if not existing_bill_for_installment:
                            bill_description = f"{bill_seed.description.split(' (Mestra)')[0]} (Parcela {installment_number_to_generate}/{bill_seed.recurring_installments_total})"
                            
                            new_generated_bill = Bill(
                                description=bill_description,
                                amount=bill_seed.amount, # Amount of one installment
                                dueDate=next_due_date_dt.isoformat(),
                                status='pending',
                                user_id=user_id,
                                recurring_transaction_origin_id=bill_seed.id, # Link back to the original recurring bill
                                installment_number=installment_number_to_generate
                            )
                            db.session.add(new_generated_bill)
                            bills_generated_count += 1
                            print(f"    GENERATED INSTALLMENT: {bill_description} for {next_due_date_dt.isoformat()}")
                        else:
                            print(f"    INSTALLMENT ALREADY EXISTS: '{bill_seed.description}' installment {existing_bill_for_installment.installment_number} on {next_due_date_dt.isoformat()}, skipping.")
                        
                        bill_seed.recurring_installments_generated = (bill_seed.recurring_installments_generated or 0) + 1
                        
                        if bill_seed.recurring_installments_generated >= bill_seed.recurring_installments_total:
                            bill_seed.is_active_recurring = False 
                            print(f"    Recurring Bill '{bill_seed.description}' deactivated: all installments generated.")
                    else: # All installments already generated
                        bill_seed.is_active_recurring = False # Ensure it's deactivated
                        print(f"    Recurring Bill '{bill_seed.description}' already generated all installments, deactivated.")
                
                else: # Non-installment recurring expenses (monthly, weekly, yearly) generate new Bill entries
                    # Check if a Bill linked to this recurring origin and due date already exists
                    existing_bill = Bill.query.filter_by(
                        recurring_transaction_origin_id=bill_seed.id,
                        dueDate=next_due_date_dt.isoformat(),
                        user_id=user_id,
                        installment_number=None # Ensure it's a non-installment recurring Bill
                    ).first()

                    if not existing_bill:
                        new_generated_bill = Bill(
                            description=bill_seed.description,
                            amount=bill_seed.amount,
                            dueDate=next_due_date_dt.isoformat(),
                            status='pending',
                            user_id=user_id,
                            is_recurring=False, # The generated bill is not itself a recurring seed
                            recurring_transaction_origin_id=bill_seed.id, # Link to recurring origin
                            installment_number=None
                        )
                        db.session.add(new_generated_bill)
                        bills_generated_count += 1
                        print(f"    GENERATED FIXED BILL: {new_generated_bill.description} for {next_due_date_dt.isoformat()}")
                    else:
                        print(f"    FIXED BILL ALREADY EXISTS: '{bill_seed.description}' on {next_due_date_dt.isoformat()}, skipping.")
            
            elif bill_seed.type == 'income': # Recurring income generates Transaction entries
                # For recurring income, we generate a normal Transaction.
                # Check for existing Transaction (relying on description, date, user_id for uniqueness)
                existing_transaction = Transaction.query.filter_by(
                    description=bill_seed.description, 
                    date=next_due_date_dt.isoformat(),
                    user_id=user_id
                ).first()
                
                if not existing_transaction:
                    # add_transaction_db already commits, but we want to commit all together later.
                    # Temporarily, add_transaction_db will commit its own.
                    # If this causes issues, we'd need to change add_transaction_db to not commit.
                    new_income_transaction = Transaction(
                        description=bill_seed.description,
                        amount=bill_seed.amount,
                        date=next_due_date_dt.isoformat(),
                        type=bill_seed.type,
                        user_id=bill_seed.user_id,
                        category_id=bill_seed.category_id
                    )
                    db.session.add(new_income_transaction) # Add to session for later commit
                    transactions_generated_count += 1
                    print(f"  GENERATED INCOME: {bill_seed.description} for {next_due_date_dt.isoformat()}")
                else:
                    print(f"  INCOME ALREADY EXISTS: '{bill_seed.description}' on {next_due_date_dt.isoformat()}, skipping.")


            # --- ADVANCE NEXT DUE DATE for the recurring_seed_bill ---
            # Always advance the date within the 'while' loop to ensure it progresses.
            # The 'bill_seed.is_active_recurring' in the 'while' loop already controls when to stop processing.
            if bill_seed.is_active_recurring: # Only advance date if the recurring master is still active
                if bill_seed.recurring_frequency == 'monthly' or bill_seed.recurring_frequency == 'installments':
                    next_due_date_dt += relativedelta(months=1)
                elif bill_seed.recurring_frequency == 'weekly':
                    next_due_date_dt += relativedelta(weeks=1)
                elif bill_seed.recurring_frequency == 'yearly':
                    next_due_date_dt += relativedelta(years=1)
                
                bill_seed.recurring_next_due_date = next_due_date_dt.isoformat()
                print(f"    Next due date updated to: {bill_seed.recurring_next_due_date}")
            else: # If the recurring bill was deactivated in this loop, its next_due_date should stay as is
                print(f"    Recurring Bill '{bill_seed.description}' deactivated, not advancing date further.")
                
            db.session.add(bill_seed) # Add the updated recurring bill (seed) to the session
            loop_counter += 1 # Increment the safety loop counter
            
            # Safety break for potential infinite loops if date calculation fails to advance
            if loop_counter >= MAX_GENERATIONS_PER_RUN and next_due_date_dt <= today:
                print(f"WARNING: Max generations reached for '{bill_seed.description}'. Deactivating to prevent infinite loop. Check recurring_start_date and recurring_frequency.")
                bill_seed.is_active_recurring = False # Force deactivate if limit reached and still due
                flash(f"Aviso: Recorrência '{bill_seed.description}' desativada. Data não avança corretamente. Verifique a data de início e frequência.", 'warning')

    db.session.commit() # Commit all changes from this processing batch (generated Bills/Transactions and updated recurring_seed_bills)

    if bills_generated_count > 0 or transactions_generated_count > 0:
        flash(f"{bills_generated_count} novas contas e {transactions_generated_count} transações geradas automaticamente!", 'info')
    else:
        print("Nenhuma conta ou transação recorrente gerada nesta execução.") # Debug


def add_transaction_db(description, amount, date, type, user_id, category_id=None):
    new_transaction = Transaction(
        description=description,
        amount=float(amount),
        date=date,
        type=type,
        user_id=user_id,
        category_id=category_id
    )
    db.session.add(new_transaction)
    db.session.commit() # Commit here to ensure transaction is saved immediately


# MODIFICADA: add_bill_db agora pode criar Bills recorrentes
def add_bill_db(description, amount, due_date, user_id, 
                is_recurring=False, recurring_frequency=None, recurring_installments_total=0):
    
    # Validação para parcelas
    if is_recurring and recurring_frequency == 'installments' and (recurring_installments_total is None or recurring_installments_total < 1):
        recurring_installments_total = 1 # Pelo menos 1 parcela se for parcelado
    elif not is_recurring or recurring_frequency != 'installments':
        recurring_installments_total = 0 # Não parcelado

    new_bill = Bill(
        description=description,
        amount=float(amount),
        dueDate=due_date,
        status='pending',
        user_id=user_id,
        is_recurring=is_recurring,
        recurring_frequency=recurring_frequency if is_recurring else None,
        recurring_start_date=due_date if is_recurring else None, # Data de início é a primeira due_date
        recurring_next_due_date=due_date if is_recurring else None, # Próxima a ser gerada é a primeira due_date
        recurring_installments_total=recurring_installments_total,
        recurring_installments_generated=0, # Inicia com 0 geradas
        is_active_recurring=is_recurring # Ativa se for recorrente
    )
    db.session.add(new_bill)
    db.session.commit() # Comita imediatamente para obter o ID da Bill, se necessário


def pay_bill_db(bill_id, user_id):
    bill = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if bill:
        bill.status = 'paid'
        db.session.add(bill)
        
        fixed_bills_category = Category.query.filter_by(name='Contas Fixas', type='expense').first()
        category_id_for_payment = fixed_bills_category.id if fixed_bills_category else None

        add_transaction_db( # Adiciona uma transação comum de DESPESA para o registro de gastos
            f"Pagamento: {bill.description}",
            bill.amount,
            datetime.date.today().isoformat(),
            'expense',
            user_id,
            category_id=category_id_for_payment
        )
        
        # Se a conta paga veio de uma recorrência, processa a próxima imediatamente
        if bill.recurring_transaction_origin_id: # Se esta Bill foi gerada por uma outra Bill recorrente
            # Encontre a Bill original que "semeou" esta recorrência
            original_recurring_bill = Bill.query.filter_by(
                id=bill.recurring_transaction_origin_id, 
                user_id=user_id,
                is_recurring=True # Garante que estamos pegando a Bill mestra recorrente
            ).first()

            if original_recurring_bill and original_recurring_bill.is_active_recurring:
                print(f"Pagamento de parcela/recorrência '{bill.description}'. Processando a origem '{original_recurring_bill.description}' para próxima geração.")
                process_recurring_bills(user_id) # Chama a função principal de processamento
            else:
                print(f"Conta paga mas origem recorrente não encontrada ou inativa: {bill.recurring_transaction_origin_id}")
        elif bill.is_recurring and bill.is_active_recurring: # Se a própria Bill é a "semente" da recorrência e ainda está ativa
            # Isso é para o caso de a Bill ser a primeira ocorrência recorrente que o usuário adicionou
            print(f"Pagamento da Bill recorrente '{bill.description}'. Processando a si mesma para próxima geração.")
            process_recurring_bills(user_id) # Re-processa todas as recorrentes (incluindo esta)
        
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

# MODIFICADA: delete_bill_db para lidar com recorrências mestras
def delete_bill_db(bill_id, user_id):
    bill = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if bill:
        # Se a Bill é uma Bill recorrente "mestra", desativa a recorrência
        if bill.is_recurring:
            bill.is_active_recurring = False
            bill.recurring_next_due_date = bill.dueDate # Congela a próxima data na atual
            db.session.add(bill) # Adiciona a alteração de status
            
            # Opcional: Deletar todas as Bills PENDENTES geradas por esta recorrência mestra
            generated_bills = Bill.query.filter_by(recurring_transaction_origin_id=bill.id, user_id=user_id, status='pending').all()
            for gen_bill in generated_bills:
                db.session.delete(gen_bill)
            print(f"Recorrência mestra '{bill.description}' desativada e {len(generated_bills)} contas geradas pendentes excluídas.")


        # Se a Bill é uma ocorrência gerada por outra recorrência, apenas a deleta
        # Se a Bill não é recorrente nem gerada por recorrência, apenas a deleta
        db.session.delete(bill)
        db.session.commit()
        return True
    return False

def edit_transaction_db(transaction_id, description, amount, date, type, user_id, category_id=None):
    transaction = Transaction.query.filter_by(id=transaction_id, user_id=user_id).first()
    if transaction:
        transaction.description = description
        transaction.amount = float(amount)
        transaction.date = date
        transaction.type = type
        transaction.category_id = category_id
        db.session.commit()
        return True
    return False

# MODIFICADA: edit_bill_db para editar campos de recorrência
def edit_bill_db(bill_id, description, amount, dueDate, user_id, 
                 is_recurring=False, recurring_frequency=None, recurring_installments_total=0, is_active_recurring=False): # Default updated
    bill = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if bill:
        bill.description = description
        bill.amount = float(amount)
        bill.dueDate = dueDate
        
        # Atualiza campos de recorrência
        bill.is_recurring = is_recurring
        bill.is_active_recurring = is_active_recurring # Controla se a recorrência mestra está ativa
        
        if is_recurring:
            bill.recurring_frequency = recurring_frequency
            # A recurring_start_date e recurring_next_due_date não mudam na edição de uma Bill existente,
            # a menos que seja uma edição da "semente" original para mudar o ponto de partida.
            # Por simplicidade, assumimos que a data de início original e a próxima data gerada
            # são persistentes após a criação da primeira Bill recorrente.
            # Se a frequência for de parcelas, atualiza o total
            if recurring_frequency == 'installments':
                bill.recurring_installments_total = recurring_installments_total if recurring_installments_total else 1
            else:
                bill.recurring_installments_total = 0 # Não é parcelado

            # Se a recorrência foi REATIVADA (ou criada como ativa), e a próxima data de vencimento
            # é anterior à data de início, ajuste-a para a data de início.
            # Isso ajuda a recalcular o next_due_date ao reativar.
            if is_active_recurring and datetime.datetime.strptime(bill.recurring_next_due_date, '%Y-%m-%d').date() < datetime.datetime.strptime(bill.recurring_start_date, '%Y-%m-%d').date():
                 bill.recurring_next_due_date = bill.recurring_start_date
                 print(f"Recorrência reativada para '{bill.description}', next_due_date resetada para start_date.")

        else: # Se não é mais recorrente
            bill.recurring_frequency = None
            bill.recurring_start_date = None
            bill.recurring_next_due_date = None
            bill.recurring_installments_total = 0
            bill.recurring_installments_generated = 0
            bill.is_active_recurring = False # Garante que está inativa

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

# --- Rotas da Aplicação ---

@app.route('/')
@login_required
def index():
    # Processa as transações recorrentes do usuário ANTES de carregar a página
    # para que as contas/transações geradas apareçam no dashboard.
    process_recurring_bills(current_user.id) 
    
    dashboard_data = get_dashboard_data_db(current_user.id)
    
    transactions_query = Transaction.query.filter_by(user_id=current_user.id)

    transaction_type_filter = request.args.get('transaction_type')
    if transaction_type_filter and transaction_type_filter in ['income', 'expense']:
        transactions_query = transactions_query.filter_by(type=transaction_type_filter)

    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    if start_date:
        transactions_query = transactions_query.filter(db.cast(Transaction.date, db.Date) >= db.cast(start_date, db.Date))
    if end_date:
        transactions_query = transactions_query.filter(db.cast(Transaction.date, db.Date) <= db.cast(end_date, db.Date))

    category_filter_id = request.args.get('category_filter', type=int)
    if category_filter_id:
        transactions_query = transactions_query.filter_by(category_id=category_filter_id)

    sort_by_transactions = request.args.get('sort_by_transactions', 'date')
    order_transactions = request.args.get('order_transactions', 'desc')

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
    
    income_transactions = [t for t in all_transactions if t.type == 'income']
    expense_transactions = [t for t in all_transactions if t.type == 'expense']


    bills_query = Bill.query.filter_by(user_id=current_user.id)

    bill_status_filter = request.args.get('bill_status')
    if bill_status_filter and bill_status_filter in ['pending', 'paid', 'overdue']:
        if bill_status_filter == 'overdue':
            today_str = datetime.date.today().isoformat()
            bills_query = bills_query.filter(Bill.dueDate < today_str, Bill.status == 'pending')
        else:
            bills_query = bills_query.filter_by(status=bill_status_filter)
    elif not bill_status_filter:
         bills_query = bills_query.filter_by(status='pending')

    sort_by_bills = request.args.get('sort_by_bills', 'dueDate')
    order_bills = request.args.get('order_bills', 'asc')

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
            
    filtered_bills = bills_query.all()

    all_categories_formatted = [(c.id, c.type, c.name) for c in Category.query.all()]
    

    return render_template(
        'index.html',
        dashboard=dashboard_data,
        transactions=all_transactions,
        bills=filtered_bills,
        income_transactions=income_transactions,
        expense_transactions=expense_transactions,
        current_date=datetime.date.today().isoformat(),
        current_user=current_user,
        current_transaction_type_filter=transaction_type_filter,
        current_bill_status_filter=bill_status_filter,
        current_sort_by_transactions=sort_by_transactions,
        current_order_transactions=order_transactions,
        current_sort_by_bills=sort_by_bills,
        current_order_bills=order_bills,
        current_start_date=start_date,
        current_end_date=end_date,
        all_categories=all_categories_formatted,
        current_category_filter=category_filter_id
    )

@app.route('/add_transaction', methods=['POST'])
@login_required
def handle_add_transaction():
    description = request.form['description']
    amount = request.form['amount']
    date = request.form['date']
    transaction_type = request.form['type']
    category_id = request.form.get('category_id', type=int)

    add_transaction_db(description, amount, date, transaction_type, current_user.id, category_id)
    flash('Transação adicionada com sucesso!', 'success')
    return redirect(url_for('index'))

@app.route('/add_bill', methods=['POST'])
@login_required
def handle_add_bill():
    description = request.form['bill_description']
    amount = request.form['bill_amount']
    due_date = request.form['bill_due_date']
    
    # NOVOS CAMPOS: Detalhes de recorrência do formulário
    is_recurring = request.form.get('is_recurring_bill') == 'on' # Checkbox retorna 'on' ou None
    recurring_frequency = request.form.get('recurring_frequency_bill')
    recurring_installments_total = request.form.get('recurring_installments_total_bill', type=int)

    add_bill_db(description, amount, due_date, current_user.id, 
                is_recurring, recurring_frequency, recurring_installments_total)
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
            'type': transaction.type,
            'category_id': transaction.category_id
        })
    return jsonify({'error': 'Transação não encontrada ou não pertence a este usuário'}), 404

@app.route('/edit_transaction/<int:transaction_id>', methods=['POST'])
@login_required
def handle_edit_transaction(transaction_id):
    description = request.form['edit_description']
    amount = request.form['edit_amount']
    date = request.form['edit_date']
    transaction_type = request.form['edit_type']
    category_id = request.form.get('edit_category_id', type=int)

    if edit_transaction_db(transaction_id, description, amount, date, transaction_type, current_user.id, category_id):
        flash('Transação atualizada com sucesso!', 'success')
    else:
        flash('Não foi possível atualizar a transação. Verifique se ela existe ou pertence a você.', 'danger')
    return redirect(url_for('index'))

# ROTA: Obter dados de uma Bill para edição (AGORA INCLUI DADOS DE RECORRÊNCIA)
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
            'status': bill.status,
            'is_recurring': bill.is_recurring,
            'recurring_frequency': bill.recurring_frequency,
            'recurring_start_date': bill.recurring_start_date,
            'recurring_next_due_date': bill.recurring_next_due_date,
            'recurring_installments_total': bill.recurring_installments_total,
            'recurring_installments_generated': bill.recurring_installments_generated,
            'is_active_recurring': bill.is_active_recurring
        })
    return jsonify({'error': 'Conta não encontrada ou não pertence a este usuário'}), 404

# ROTA: Salvar edições de uma Bill (AGORA INCLUI DADOS DE RECORRÊNCIA)
@app.route('/edit_bill/<int:bill_id>', methods=['POST'])
@login_required
def handle_edit_bill(bill_id):
    description = request.form['edit_bill_description']
    amount = request.form['edit_bill_amount']
    due_date = request.form['edit_bill_dueDate']
    
    # NOVOS CAMPOS: Detalhes de recorrência do formulário de edição
    is_recurring = request.form.get('edit_is_recurring_bill') == 'on'
    recurring_frequency = request.form.get('edit_recurring_frequency_bill')
    recurring_installments_total = request.form.get('edit_recurring_installments_total_bill', type=int)
    is_active_recurring = request.form.get('edit_is_active_recurring_bill') == 'on'

    if edit_bill_db(bill_id, description, amount, due_date, current_user.id,
                    is_recurring, recurring_frequency, recurring_installments_total, is_active_recurring):
        flash('Conta atualizada com sucesso!', 'success')
    else:
        flash('Não foi possível atualizar a conta. Verifique se ela existe ou pertence a você.', 'danger')
    return redirect(url_for('index'))


# --- ROTAS DE AUTENTICAÇÃO E GERENCIAMENTO DE USUÁRIO ---

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

    target_month_str = f"{year}-{month:02d}"

    monthly_transactions = Transaction.query.filter(
        Transaction.user_id == current_user.id,
        Transaction.date.like(f"{target_month_str}-%")
    ).all()

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
            {'description': t.description, 'amount': t.amount, 'type': t.type, 'date': t.date,
             'category': t.category.name if t.category else 'Sem Categoria'}
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
        f"Detalhes das transações (descrição, valor, tipo, data, categoria): {monthly_summary['transactions_details']}. "
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

@app.route('/get_chart_data', methods=['GET'])
@login_required
def get_chart_data():
    user_id = current_user.id
    
    today = datetime.date.today()
    
    month_labels = []
    monthly_income_data = {}
    monthly_expenses_data = {}

    for i in range(6, -1, -1): # Últimos 7 meses (mês atual + 6 anteriores)
        target_month = today.month - i
        target_year = today.year
        while target_month <= 0:
            target_month += 12
            target_year -= 1
        
        month_name = datetime.date(target_year, target_month, 1).strftime('%b/%Y')
        month_labels.append(month_name)
        
        target_month_str = f"{target_year}-{target_month:02d}"
        transactions_in_month = Transaction.query.filter(
            Transaction.user_id == user_id,
            Transaction.date.like(f"{target_month_str}-%")
        ).all()
        
        monthly_income_data[month_name] = sum(t.amount for t in transactions_in_month if t.type == 'income')
        monthly_expenses_data[month_name] = sum(t.amount for t in transactions_in_month if t.type == 'expense')

    monthly_overview_chart_data = {
        'labels': month_labels,
        'income': [monthly_income_data[m] for m in month_labels],
        'expenses': [monthly_expenses_data[m] for m in month_labels]
    }

    expenses_by_category = {}
    
    current_year_str = str(today.year)
    current_year_transactions = Transaction.query.filter(
        Transaction.user_id == user_id,
        Transaction.date.like(f"{current_year_str}-%"),
        Transaction.type == 'expense'
    ).all()

    for transaction in current_year_transactions:
        category_name = transaction.category.name if transaction.category else 'Sem Categoria'
        expenses_by_category[category_name] = expenses_by_category.get(category_name, 0) + transaction.amount

    expenses_by_category_chart_data = {
        'labels': list(expenses_by_category.keys()),
        'values': list(expenses_by_category.values())
    }

    return jsonify({
        'monthly_summary': monthly_overview_chart_data,
        'expenses_by_category': expenses_by_category_chart_data
    })

# REMOVIDO: Rota '/recurring_transactions' (será uma funcionalidade do /index)
# @app.route('/recurring_transactions')
# @login_required
# def recurring_transactions():
#    ...

# REMOVIDO: Rota '/add_recurring_transaction' (agora a Bill é criada diretamente)
# @app.route('/add_recurring_transaction', methods=['POST'])
# @login_required
# def handle_add_recurring_transaction():
#    ...

# REMOVIDO: Rota '/get_recurring_transaction_data/<int:recurring_id>'
# @app.route('/get_recurring_transaction_data/<int:recurring_id>', methods=['GET'])
# @login_required
# def get_recurring_transaction_data(recurring_id):
#    ...

# REMOVIDO: Rota '/edit_recurring_transaction/<int:recurring_id>'
# @app.route('/edit_recurring_transaction/<int:recurring_id>', methods=['POST'])
# @login_required
# def handle_edit_recurring_transaction(recurring_id):
#    ...

# REMOVIDO: Rota '/delete_recurring_transaction/<int:recurring_id>'
# @app.route('/delete_recurring_transaction/<int:recurring_id>', methods=['POST'])
# @login_required
# def handle_delete_recurring_transaction(recurring_id):
#    ...


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        
        if not Category.query.first():
            db.session.add(Category(name='Salário', type='income'))
            db.session.add(Category(name='Freelance', type='income'))
            db.session.add(Category(name='Outras Receitas', type='income'))
            db.session.add(Category(name='Alimentação', type='expense'))
            db.session.add(Category(name='Transporte', type='expense'))
            db.session.add(Category(name='Lazer', type='expense'))
            db.session.add(Category(name='Moradia', type='expense'))
            db.session.add(Category(name='Saúde', type='expense'))
            db.session.add(Category(name='Educação', type='expense'))
            db.session.add(Category(name='Contas Fixas', type='expense'))
            db.session.add(Category(name='Outras Despesas', type='expense'))
            db.session.commit()
            print("Categorias padrão adicionadas.")

        if not User.query.filter_by(username='admin').first():
            admin_user = User(username='admin')
            admin_user.set_password('admin123')
            db.session.add(admin_user)
            db.session.commit()
            print("Usuário 'admin' criado com senha 'admin123'.")

        # Dados iniciais para contas a pagar e transações recorrentes (associados ao primeiro usuário, se houver)
        if User.query.first():
            first_user = User.query.first()
            
            # Limpa Bills existentes para garantir um teste limpo com novas gerações
            # Isso é apenas para desenvolvimento/teste. Não faça isso em produção com dados reais!
            # Bill.query.filter_by(user_id=first_user.id).delete()
            # db.session.commit()

            # Transações recorrentes de exemplo
            # Garante que as recorrências de exemplo só sejam adicionadas se NÃO EXISTIREM para o usuário
            # E se NÃO EXISTIR uma Bill Mestra de recorrência já para este tipo
            if not Bill.query.filter(Bill.user_id==first_user.id, Bill.is_recurring==True).first():
                salario_category = Category.query.filter_by(name='Salário', type='income').first()
                contas_fixas_category = Category.query.filter_by(name='Contas Fixas', type='expense').first()
                
                # Exemplo 1: Salário Mensal (receita, NÃO gera Bill)
                db.session.add(Transaction( # Adiciona uma transação comum
                    description='Salário Mensal (Recorrente Exemplo)', # Mudei a descrição para ser mais única
                    amount=3000.00,
                    type='income',
                    date='2025-01-01', # Ajuste para o passado para forçar geração imediata
                    user_id=first_user.id,
                    category_id=salario_category.id if salario_category else None
                ))

                # Exemplo 2: Aluguel Apartamento (despesa, recorrente mensal - GERA BILL)
                db.session.add(Bill(
                    description='Aluguel Apartamento (Recorrente)',
                    amount=1500.00,
                    dueDate='2025-01-05', # Data da primeira ocorrência a ser gerada
                    status='pending',
                    user_id=first_user.id,
                    is_recurring=True,
                    recurring_frequency='monthly',
                    recurring_start_date='2025-01-05',
                    recurring_next_due_date='2025-01-05', # Força a geração para o mês atual/passado
                    recurring_installments_total=0,
                    recurring_installments_generated=0,
                    is_active_recurring=True
                ))
                
                # Exemplo 3: Internet Fibra (despesa, recorrente mensal - GERA BILL)
                db.session.add(Bill(
                    description='Internet Fibra (Recorrente)',
                    amount=99.90,
                    dueDate='2025-01-10', # Data da primeira ocorrência a ser gerada
                    status='pending',
                    user_id=first_user.id,
                    is_recurring=True,
                    recurring_frequency='monthly',
                    recurring_start_date='2025-01-10',
                    recurring_next_due_date='2025-01-10', # Força a geração para o mês atual/passado
                    recurring_installments_total=0,
                    recurring_installments_generated=0,
                    is_active_recurring=True
                ))

                # Exemplo 4: Compra Parcelada Tênis (despesa, parcelada - GERA BILLS SEQUENCIAIS)
                db.session.add(Bill(
                    description='Compra Parcelada Tênis (Mestra)', # Esta é a Bill "mestra" que gerará parcelas
                    amount=100.00, # Valor de UMA parcela
                    dueDate='2025-03-01', # Data da primeira parcela a ser gerada (coloquei 1º do mês para teste)
                    status='pending', # O status aqui pode ser irrelevante para a mestra, mas mantemos
                    user_id=first_user.id,
                    is_recurring=True,
                    recurring_frequency='installments',
                    recurring_start_date='2025-03-01',
                    recurring_next_due_date='2025-03-01', # Força a geração da primeira parcela para o passado
                    recurring_installments_total=5, # Total de 5 parcelas
                    recurring_installments_generated=0, # Começa do zero para teste
                    is_active_recurring=True
                ))
                
                db.session.commit()
                print("Contas e transações recorrentes de exemplo adicionadas.")


    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
