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
    recurring_transactions = db.relationship('RecurringTransaction', backref='user', lazy=True, cascade='all, delete-orphan')


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
    recurring_transactions = db.relationship('RecurringTransaction', backref='category', lazy=True)

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

class Bill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    dueDate = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    recurring_transaction_origin_id = db.Column(db.Integer, db.ForeignKey('recurring_transaction.id'), nullable=True)
    installment_number = db.Column(db.Integer, nullable=True) # Número da parcela

    def __repr__(self):
        return f"<Bill {self.description} - {self.dueDate} - {self.status}>"

class RecurringTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(10), nullable=False) # 'income' ou 'expense'
    frequency = db.Column(db.String(20), nullable=False) # 'monthly', 'weekly', 'yearly', 'installments'
    start_date = db.Column(db.String(10), nullable=False) # Data de início
    next_due_date = db.Column(db.String(10), nullable=False) # Próxima data para gerar a transação
    is_active = db.Column(db.Boolean, default=True, nullable=False) # Ativa ou desativa
    
    installments_total = db.Column(db.Integer, nullable=True, default=0) # Total de parcelas (0 para indefinido/não-parcelado)
    installments_generated = db.Column(db.Integer, nullable=True, default=0) # Quantas parcelas já foram geradas como Bill/Transaction

    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)

    def __repr__(self):
        return f"<RecurringTransaction {self.description} - {self.frequency}>"


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

# MODIFICADA: Adicionar Transação Recorrente com campos de parcelamento
def add_recurring_transaction_db(description, amount, type, frequency, start_date, user_id, category_id=None, installments_total=0):
    # Garante que installments_total seja pelo menos 1 se for 'installments', ou 0 se não for.
    if frequency == 'installments' and (installments_total is None or installments_total < 1):
        installments_total = 1 # Garante pelo menos 1 parcela se a frequência for 'installments'
    elif frequency != 'installments':
        installments_total = 0 # Não é parcelado

    new_recurring_transaction = RecurringTransaction(
        description=description,
        amount=float(amount),
        type=type,
        frequency=frequency,
        start_date=start_date,
        next_due_date=start_date, # A primeira next_due_date é a start_date
        is_active=True,
        user_id=user_id,
        category_id=category_id,
        installments_total=installments_total,
        installments_generated=0 # Começa com 0 parcelas geradas
    )
    db.session.add(new_recurring_transaction)
    db.session.commit()

def get_recurring_transactions_db(user_id):
    return RecurringTransaction.query.filter_by(user_id=user_id).all()

# MODIFICADA: Edita Transação Recorrente com campos de parcelamento
def edit_recurring_transaction_db(recurring_id, description, amount, type, frequency, start_date, user_id, category_id=None, is_active=True, installments_total=0):
    recurring_trans = RecurringTransaction.query.filter_by(id=recurring_id, user_id=user_id).first()
    if recurring_trans:
        recurring_trans.description = description
        recurring_trans.amount = float(amount)
        recurring_trans.type = type
        recurring_trans.frequency = frequency
        recurring_trans.start_date = start_date
        recurring_trans.category_id = category_id
        recurring_trans.is_active = is_active
        
        if frequency == 'installments' and (installments_total is None or installments_total < 1):
            recurring_trans.installments_total = 1
        elif frequency != 'installments':
            recurring_trans.installments_total = 0
        else:
            recurring_trans.installments_total = installments_total

        db.session.commit()
        return True
    return False

def delete_recurring_transaction_db(recurring_id, user_id):
    recurring_trans = RecurringTransaction.query.filter_by(id=recurring_id, user_id=user_id).first()
    if recurring_trans:
        # Quando uma recorrência é deletada, também deletar as Bills geradas por ela
        # que ainda não foram pagas ou que ainda estão ativas.
        # Isso evita "órfãos" no Contas a Pagar.
        bills_from_this_recurring = Bill.query.filter_by(recurring_transaction_origin_id=recurring_id, user_id=user_id, status='pending').all()
        for bill_to_delete in bills_from_this_recurring:
            db.session.delete(bill_to_delete)
        
        db.session.delete(recurring_trans)
        db.session.commit()
        return True
    return False


def pay_bill_db(bill_id, user_id):
    bill = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if bill:
        bill.status = 'paid'
        db.session.add(bill) # Adiciona a alteração de status da Bill
        
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
        # O commit para a transaction está dentro de add_transaction_db
        # e o commit para a bill está no final desta função

        # Se a conta paga veio de uma recorrência, processa a próxima imediatamente
        # Isso garantirá que a próxima parcela seja gerada se for devido.
        if bill.recurring_transaction_origin_id:
            rec_trans_origin = RecurringTransaction.query.get(bill.recurring_transaction_origin_id)
            if rec_trans_origin and rec_trans_origin.user_id == user_id:
                # Chamamos process_due_recurring_transactions aqui para gerar a próxima ocorrência
                # É importante ter certeza de que essa chamada não crie um loop infinito ou duplique coisas
                print(f"Processando recorrências após pagamento da conta {bill.id} de origem {bill.recurring_transaction_origin_id}")
                process_due_recurring_transactions(user_id) # Chama a função principal de processamento
        
        db.session.commit() # Comita todas as alterações (bill.status e outras)
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

# --- FUNÇÃO PARA PROCESSAR TRANSAÇÕES RECORRENTES PENDENTES ---
def process_due_recurring_transactions(user_id):
    today = datetime.date.today()
    bills_generated_count = 0
    transactions_generated_count = 0

    recurring_transactions = RecurringTransaction.query.filter_by(user_id=user_id, is_active=True).all()
    
    for rec_trans in recurring_transactions:
        # Convert next_due_date to datetime.date object for comparison and calculation
        next_due_date_dt = datetime.datetime.strptime(rec_trans.next_due_date, '%Y-%m-%d').date()
        
        # Loop while the next due date is today or in the past AND the recurring transaction is active
        # Adicionado um limite de loop para evitar problemas em caso de datas muito antigas/bugadas
        loop_counter = 0
        MAX_GENERATIONS_PER_RUN = 36 # Aumentado para gerar mais ocorrências passadas se necessário (ex: 3 anos)
        
        print(f"\n--- Processando Recorrência: '{rec_trans.description}' (ID: {rec_trans.id}) ---") # Debug

        while next_due_date_dt <= today and rec_trans.is_active and loop_counter < MAX_GENERATIONS_PER_RUN:
            print(f"  Verificando data: {next_due_date_dt}, Hoje: {today}") # Debug
            
            # --- Lógica de Geração ---
            
            # Caso 1: Frequência 'installments' (parcelas)
            if rec_trans.frequency == 'installments':
                # Verifica se ainda há parcelas a serem geradas
                if (rec_trans.installments_generated or 0) < rec_trans.installments_total:
                    installment_number_to_generate = (rec_trans.installments_generated or 0) + 1
                    # Verifica se a conta para esta parcela e data já foi gerada para EVITAR DUPLICATAS
                    existing_bill_for_installment = Bill.query.filter_by(
                        recurring_transaction_origin_id=rec_trans.id,
                        dueDate=next_due_date_dt.isoformat(),
                        installment_number=installment_number_to_generate, # Verifica o número da parcela também
                        user_id=user_id
                    ).first()

                    if not existing_bill_for_installment:
                        bill_description = f"{rec_trans.description} (Parcela {installment_number_to_generate}/{rec_trans.installments_total})"
                        
                        new_bill = Bill(
                            description=bill_description,
                            amount=rec_trans.amount, # Amount of one installment
                            dueDate=next_due_date_dt.isoformat(),
                            status='pending',
                            user_id=rec_trans.user_id,
                            recurring_transaction_origin_id=rec_trans.id,
                            installment_number=installment_number_to_generate
                        )
                        db.session.add(new_bill)
                        bills_generated_count += 1
                        print(f"  GERADA PARCELA: {bill_description} para {next_due_date_dt.isoformat()}") # Debug
                    else:
                        print(f"  PARCELA JÁ EXISTENTE: '{rec_trans.description}' parcela {existing_bill_for_installment.installment_number} em {next_due_date_dt.isoformat()}, pulando.")
                    
                    # Sempre incrementa o contador de parcelas geradas APÓS processar a data de vencimento
                    rec_trans.installments_generated = (rec_trans.installments_generated or 0) + 1
                    
                    # Desativa a recorrência se todas as parcelas foram geradas AGORA
                    if rec_trans.installments_generated >= rec_trans.installments_total:
                        rec_trans.is_active = False 
                        print(f"  Recorrência '{rec_trans.description}' desativada: todas as parcelas geradas.")
                else: # Todas as parcelas já foram geradas em execuções anteriores
                    rec_trans.is_active = False # Garante que está desativada
                    print(f"  Recorrência '{rec_trans.description}' já gerou todas as parcelas, desativada.")

            # Caso 2: Outras frequências ('monthly', 'weekly', 'yearly')
            else: # Despesas ou Receitas recorrentes NÃO PARCELADAS
                # É uma despesa recorrente que DEVE gerar uma Bill
                if rec_trans.type == 'expense':
                    # Verifica se uma Bill com esta origem e data já foi gerada (sem installment_number)
                    existing_bill_for_recurring = Bill.query.filter_by(
                        recurring_transaction_origin_id=rec_trans.id,
                        dueDate=next_due_date_dt.isoformat(),
                        user_id=user_id,
                        installment_number=None # Importante: não é uma parcela
                    ).first()

                    if not existing_bill_for_recurring:
                        new_bill = Bill(
                            description=rec_trans.description,
                            amount=rec_trans.amount,
                            dueDate=next_due_date_dt.isoformat(),
                            status='pending',
                            user_id=rec_trans.user_id,
                            recurring_transaction_origin_id=rec_trans.id, # Link to recurring origin
                            installment_number=None # No installment number for non-installment
                        )
                        db.session.add(new_bill)
                        bills_generated_count += 1
                        print(f"  GERADA CONTA FIXA: {rec_trans.description} para {next_due_date_dt.isoformat()}") # Debug
                    else:
                        print(f"  CONTA FIXA JÁ EXISTENTE: '{rec_trans.description}' em {next_due_date_dt.isoformat()}, pulando.")
                
                # É uma receita recorrente que DEVE gerar uma Transaction
                elif rec_trans.type == 'income':
                    # Verifica se uma Transaction com esta descrição e data já foi gerada
                    existing_transaction_for_recurring = Transaction.query.filter_by(
                        description=rec_trans.description, 
                        date=next_due_date_dt.isoformat(),
                        user_id=user_id
                    ).first()
                    
                    if not existing_transaction_for_recurring:
                        add_transaction_db( # Gerar Transaction para Receitas
                            description=rec_trans.description,
                            amount=rec_trans.amount,
                            date=next_due_date_dt.isoformat(),
                            type=rec_trans.type,
                            user_id=rec_trans.user_id,
                            category_id=rec_trans.category_id
                        )
                        transactions_generated_count += 1
                        print(f"  GERADA RECEITA: {rec_trans.description} para {next_due_date_dt.isoformat()}") # Debug
                    else:
                        print(f"  RECEITA JÁ EXISTENTE: '{rec_trans.description}' em {next_due_date_dt.isoformat()}, pulando.")

            # --- AVANÇA A PRÓXIMA DATA DE VENCIMENTO ---
            # Sempre avance a data dentro do loop 'while' para garantir que ele progrida.
            # A condição 'rec_trans.is_active' no 'while' loop já controla quando parar de processar mais.
            if rec_trans.is_active: # Só avança a data se a recorrência ainda estiver ativa ou acabou de ser gerada
                if rec_trans.frequency == 'monthly' or rec_trans.frequency == 'installments':
                    next_due_date_dt += relativedelta(months=1)
                elif rec_trans.frequency == 'weekly':
                    next_due_date_dt += relativedelta(weeks=1)
                elif rec_trans.frequency == 'yearly':
                    next_due_date_dt += relativedelta(years=1)
                rec_trans.next_due_date = next_due_date_dt.isoformat()
            else: # Se a recorrência foi desativada no loop (ex: todas as parcelas geradas), não avança mais a data
                rec_trans.next_due_date = rec_trans.next_due_date # Mantém a última data (que desativou a recorrência)
                
            db.session.add(rec_trans) # Adiciona a transação recorrente atualizada para a sessão
            loop_counter += 1 # Incrementa o contador de segurança do loop
            
            if loop_counter >= MAX_GENERATIONS_PER_RUN and next_due_date_dt <= today:
                print(f"WARNING: Max generations reached for '{rec_trans.description}'. Deactivating to prevent infinite loop. Check recurrence start_date and frequency.")
                rec_trans.is_active = False # Deactivate to stop future processing
                flash(f"Aviso: Recorrência '{rec_trans.description}' desativada. Data não avança corretamente. Verifique a data de início e frequência.", 'warning')

    db.session.commit() # Comita todas as mudanças de recorrências de uma vez (incluindo is_active e next_due_date)

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
    db.session.commit() # Commit aqui para garantir que a transação é salva individualmente

# MODIFICADA: Adicionar Transação Recorrente com campos de parcelamento
def add_recurring_transaction_db(description, amount, type, frequency, start_date, user_id, category_id=None, installments_total=0):
    # Garante que installments_total seja pelo menos 1 se for 'installments', ou 0 se não for.
    if frequency == 'installments' and (installments_total is None or installments_total < 1):
        installments_total = 1 # Garante pelo menos 1 parcela se a frequência for 'installments'
    elif frequency != 'installments':
        installments_total = 0 # Não é parcelado

    new_recurring_transaction = RecurringTransaction(
        description=description,
        amount=float(amount),
        type=type,
        frequency=frequency,
        start_date=start_date,
        next_due_date=start_date, # A primeira next_due_date é a start_date
        is_active=True,
        user_id=user_id,
        category_id=category_id,
        installments_total=installments_total,
        installments_generated=0 # Começa com 0 parcelas geradas
    )
    db.session.add(new_recurring_transaction)
    db.session.commit()

def get_recurring_transactions_db(user_id):
    return RecurringTransaction.query.filter_by(user_id=user_id).all()

# MODIFICADA: Edita Transação Recorrente com campos de parcelamento
def edit_recurring_transaction_db(recurring_id, description, amount, type, frequency, start_date, user_id, category_id=None, is_active=True, installments_total=0):
    recurring_trans = RecurringTransaction.query.filter_by(id=recurring_id, user_id=user_id).first()
    if recurring_trans:
        recurring_trans.description = description
        recurring_trans.amount = float(amount)
        recurring_trans.type = type
        recurring_trans.frequency = frequency
        recurring_trans.start_date = start_date
        recurring_trans.category_id = category_id
        recurring_trans.is_active = is_active
        
        if frequency == 'installments' and (installments_total is None or installments_total < 1):
            recurring_trans.installments_total = 1
        elif frequency != 'installments':
            recurring_trans.installments_total = 0
        else:
            recurring_trans.installments_total = installments_total

        db.session.commit()
        return True
    return False

def delete_recurring_transaction_db(recurring_id, user_id):
    recurring_trans = RecurringTransaction.query.filter_by(id=recurring_id, user_id=user_id).first()
    if recurring_trans:
        # Quando uma recorrência é deletada, também deletar as Bills geradas por ela
        # que ainda não foram pagas ou que ainda estão ativas.
        # Isso evita "órfãos" no Contas a Pagar.
        bills_from_this_recurring = Bill.query.filter_by(recurring_transaction_origin_id=recurring_id, user_id=user_id, status='pending').all()
        for bill_to_delete in bills_from_this_recurring:
            db.session.delete(bill_to_delete)
        
        db.session.delete(recurring_trans)
        db.session.commit()
        return True
    return False


def pay_bill_db(bill_id, user_id):
    bill = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if bill:
        bill.status = 'paid'
        db.session.add(bill)
        
        fixed_bills_category = Category.query.filter_by(name='Contas Fixas', type='expense').first()
        category_id_for_payment = fixed_bills_category.id if fixed_bills_category else None

        add_transaction_db(
            f"Pagamento: {bill.description}",
            bill.amount,
            datetime.date.today().isoformat(),
            'expense',
            user_id,
            category_id=category_id_for_payment
        )
        # O commit para a transaction está dentro de add_transaction_db
        # e o commit para a bill está no final desta função

        # Se a conta paga veio de uma recorrência, processa a próxima imediatamente
        if bill.recurring_transaction_origin_id:
            rec_trans_origin = RecurringTransaction.query.get(bill.recurring_transaction_origin_id)
            if rec_trans_origin and rec_trans_origin.user_id == user_id:
                # Chamamos process_due_recurring_transactions aqui para gerar a próxima ocorrência
                print(f"Processando recorrências após pagamento da conta {bill.id} de origem {bill.recurring_transaction_origin_id}")
                process_due_recurring_transactions(user_id) # Chama a função principal de processamento
        
        db.session.commit() # Comita todas as alterações (bill.status e outras)
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

# --- Rotas da Aplicação ---

@app.route('/')
@login_required
def index():
    # Processa as transações recorrentes do usuário ANTES de carregar a página
    # para que as contas/transações geradas apareçam no dashboard.
    process_due_recurring_transactions(current_user.id) 
    
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

# ROTA: Página de Transações Recorrentes
@app.route('/recurring_transactions')
@login_required
def recurring_transactions(): # Endpoint é 'recurring_transactions'
    # Processa as transações recorrentes do usuário ANTES de carregar a página
    # para garantir que as contas/transações sejam geradas antes de exibir a lista
    process_due_recurring_transactions(current_user.id) 

    all_categories_formatted = [(c.id, c.type, c.name) for c in Category.query.all()]
    recurring_items = get_recurring_transactions_db(current_user.id)
    return render_template(
        'recurring_transactions.html',
        all_categories=all_categories_formatted,
        recurring_transactions=recurring_items,
        current_date=datetime.date.today().isoformat(),
        current_user=current_user
    )

# ROTA: Adicionar Transação Recorrente
@app.route('/add_recurring_transaction', methods=['POST'])
@login_required
def handle_add_recurring_transaction():
    description = request.form['recurring_description']
    amount = request.form['recurring_amount']
    transaction_type = request.form['recurring_type']
    frequency = request.form['recurring_frequency']
    start_date = request.form['recurring_start_date']
    category_id = request.form.get('recurring_category_id', type=int)

    add_recurring_transaction_db(description, amount, transaction_type, frequency, start_date, current_user.id, category_id)
    flash('Transação recorrente adicionada com sucesso!', 'success')
    return redirect(url_for('recurring_transactions'))

# ROTA: Obter dados de uma transação recorrente para edição
@app.route('/get_recurring_transaction_data/<int:recurring_id>', methods=['GET'])
@login_required
def get_recurring_transaction_data(recurring_id):
    recurring_trans = RecurringTransaction.query.filter_by(id=recurring_id, user_id=current_user.id).first()
    if recurring_trans:
        return jsonify({
            'id': recurring_trans.id,
            'description': recurring_trans.description,
            'amount': recurring_trans.amount,
            'type': recurring_trans.type,
            'frequency': recurring_trans.frequency,
            'start_date': recurring_trans.start_date,
            'category_id': recurring_trans.category_id,
            'is_active': recurring_trans.is_active
        })
    return jsonify({'error': 'Transação recorrente não encontrada ou não pertence a este usuário'}), 404

# ROTA: Salvar edições de uma transação recorrente
@app.route('/edit_recurring_transaction/<int:recurring_id>', methods=['POST'])
@login_required
def handle_edit_recurring_transaction(recurring_id):
    description = request.form['edit_recurring_description']
    amount = request.form['edit_recurring_amount']
    trans_type = request.form['edit_recurring_type']
    frequency = request.form['edit_recurring_frequency']
    start_date = request.form['edit_recurring_start_date']
    category_id = request.form.get('edit_recurring_category_id', type=int)
    is_active = request.form.get('edit_recurring_is_active') == 'on'

    if edit_recurring_transaction_db(recurring_id, description, amount, trans_type, frequency, start_date, current_user.id, category_id, is_active):
        flash('Transação recorrente atualizada com sucesso!', 'success')
    else:
        flash('Não foi possível atualizar a transação recorrente. Verifique se ela existe ou pertence a você.', 'danger')
    return redirect(url_for('recurring_transactions'))

# ROTA: Excluir Transação Recorrente
@app.route('/delete_recurring_transaction/<int:recurring_id>', methods=['POST'])
@login_required
def handle_delete_recurring_transaction(recurring_id):
    if delete_recurring_transaction_db(recurring_id, current_user.id):
        flash('Transação recorrente excluída com sucesso!', 'info')
    else:
        flash('Não foi possível excluir a transação recorrente. Verifique se ela existe ou pertence a você.', 'danger')
    return redirect(url_for('recurring_transactions'))

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
            if not RecurringTransaction.query.filter_by(user_id=first_user.id).first():
                salario_category = Category.query.filter_by(name='Salário', type='income').first()
                contas_fixas_category = Category.query.filter_by(name='Contas Fixas', type='expense').first()

                db.session.add(RecurringTransaction(
                    description='Salário Mensal',
                    amount=3000.00,
                    type='income',
                    frequency='monthly',
                    start_date='2025-01-01',
                    next_due_date='2025-01-01', # Ajuste para o passado para forçar geração imediata
                    is_active=True,
                    user_id=first_user.id,
                    category_id=salario_category.id if salario_category else None,
                    installments_total=0, # Não é parcelado (indefinido)
                    installments_generated=0
                ))
                db.session.add(RecurringTransaction(
                    description='Aluguel Apartamento',
                    amount=1500.00,
                    type='expense',
                    frequency='monthly',
                    start_date='2025-01-01',
                    next_due_date='2025-01-01', # Ajuste para o passado para forçar geração imediata
                    is_active=True,
                    user_id=first_user.id,
                    category_id=contas_fixas_category.id if contas_fixas_category else None,
                    installments_total=0,
                    installments_generated=0
                ))
                db.session.add(RecurringTransaction(
                    description='Internet Fibra',
                    amount=99.90,
                    type='expense',
                    frequency='monthly',
                    start_date='2025-01-01',
                    next_due_date='2025-01-01', # Ajuste para o passado para forçar geração imediata
                    is_active=True,
                    user_id=first_user.id,
                    category_id=contas_fixas_category.id if contas_fixas_category else None,
                    installments_total=0,
                    installments_generated=0
                ))
                db.session.add(RecurringTransaction(
                    description='Compra Parcelada Tênis',
                    amount=100.00, # Valor da parcela
                    type='expense',
                    frequency='installments',
                    start_date='2025-03-01',
                    next_due_date='2025-03-01', # Ajuste para o passado para forçar geração imediata
                    is_active=True,
                    user_id=first_user.id,
                    category_id=contas_fixas_category.id if contas_fixas_category else None, # Ou uma categoria "Parcelas"
                    installments_total=5, # Total de 5 parcelas
                    installments_generated=0 # Começa do zero para teste
                ))
                db.session.commit()
                print("Transações recorrentes de exemplo adicionadas.")

    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
