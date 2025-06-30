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
    is_master_recurring_bill = db.Column(db.Boolean, default=False, nullable=False) # É uma conta recorrente (semente)?
    recurring_parent_id = db.Column(db.Integer, db.ForeignKey('bill.id'), nullable=True) # ID da Bill mestra (para Bills geradas)
    recurring_child_number = db.Column(db.Integer, nullable=True) # REINTRODUZIDO: Número da ocorrência gerada (1, 2, 3...)
    
    recurring_frequency = db.Column(db.String(20), nullable=True) # 'monthly', 'weekly', 'yearly', 'installments'
    recurring_start_date = db.Column(db.String(10), nullable=True) # Data de início da recorrência (original da mestra)
    recurring_next_due_date = db.Column(db.String(10), nullable=True) # Próxima data de vencimento a ser gerada
    recurring_total_occurrences = db.Column(db.Integer, nullable=True) # Total de ocorrências a gerar (para mestra)
    recurring_installments_generated = db.Column(db.Integer, nullable=True, default=0) # Quantas parcelas já foram geradas/pagas
    is_active_recurring = db.Column(db.Boolean, default=False, nullable=False) # A recorrência ainda está ativa para gerar mais?
    type = db.Column(db.String(10), nullable=False, default='expense') # Tipo da Bill (expense ou income)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True) # NOVO CAMPO: Para Bills mestras de receita

    def __repr__(self):
        return f"<Bill {self.description} - {self.dueDate} - {self.status}>"

# --- NOVOS MODELOS PARA ORÇAMENTO E METAS ---
class Budget(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    budget_amount = db.Column(db.Float, nullable=False)
    month_year = db.Column(db.String(7), nullable=False) # 'YYYY-MM'
    current_spent = db.Column(db.Float, default=0.0, nullable=False)

    # Corrigido: backref deve ser única para o modelo User
    user = db.relationship('User', backref='user_budgets', lazy=True)
    category = db.relationship('Category', backref='budgets', lazy=True)

    __table_args__ = (db.UniqueConstraint('user_id', 'category_id', 'month_year', name='_user_category_month_uc'),)

    def __repr__(self):
        return f"<Budget {self.category.name} for {self.month_year}: {self.budget_amount}>"

class Goal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    target_amount = db.Column(db.Float, nullable=False)
    current_amount = db.Column(db.Float, default=0.0, nullable=False)
    due_date = db.Column(db.String(10), nullable=True) # YYYY-MM-DD
    status = db.Column(db.String(20), default='in_progress', nullable=False) # 'in_progress', 'achieved', 'abandoned'

    # Corrigido: backref deve ser única para o modelo User
    user = db.relationship('User', backref='user_goals', lazy=True)

    def __repr__(self):
        return f"<Goal {self.name}: {self.current_amount}/{self.target_amount}>"


# --- Funções de Lógica de Negócios (ORDEM OTIMIZADA) ---

# Define 'TODAY_DATE' aqui para que esteja disponível para TODAS as funções abaixo que a utilizam
TODAY_DATE = datetime.date.today()

# FUNÇÕES AUXILIARES DE ADIÇÃO/EDIÇÃO/EXCLUSÃO SIMPLES (precisam de db.session.add/commit)
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
    db.session.flush() # Use flush to get ID before commit

    # Logic to update budget current_spent for expenses
    if type == 'expense' and category_id:
        transaction_month_year = datetime.datetime.strptime(date, '%Y-%m-%d').strftime('%Y-%m')
        budget = Budget.query.filter_by(
            user_id=user_id,
            category_id=category_id,
            month_year=transaction_month_year
        ).first()
        if budget:
            budget.current_spent += float(amount)
            db.session.add(budget)
            print(f"DEBUG: Budget for category {category_id} updated. New spent: {budget.current_spent}")
        else:
            print(f"DEBUG: No active budget found for category {category_id} in {transaction_month_year} for user {user_id}. Transaction added without budget update.")
    
    db.session.commit()

# FUNÇÃO CENTRAL AUXILIAR: Gera Bills filhas (ocorrências futuras) a partir de uma Bill mestra recorrente
def _generate_future_recurring_bills(master_bill):
    print(f"DEBUG: _generate_future_recurring_bills chamada para master_bill ID: {master_bill.id}, Desc: {master_bill.description}")
    
    if not master_bill.id:
        print("ERROR: Master Bill does not have an ID yet. Cannot generate children.")
        return

    generated_count_for_master = 0
    
    # Limpa Bills filhas PENDENTES existentes que foram geradas por esta mestra
    # Esta linha pode ser ajustada para não apagar bills já pagas
    Bill.query.filter_by(recurring_parent_id=master_bill.id, user_id=master_bill.user_id, status='pending').delete()
    db.session.commit() # Comita a deleção imediatamente

    # Recalcula a próxima data de vencimento da Mestra ANTES DE GERAR
    current_occurrence_date_from_master_start = datetime.datetime.strptime(master_bill.recurring_start_date, '%Y-%m-%d').date()
    
    # Define o total de ocorrências a gerar.
    # Se 0 (indefinido), gera 12 ocorrências (1 ano de meses ou 12 semanas para semanaais, etc.).
    # Isso evita uma geração excessiva para recorrências "indefinidas".
    total_to_generate = master_bill.recurring_total_occurrences if master_bill.recurring_total_occurrences and master_bill.recurring_total_occurrences > 0 else 12
    
    print(f"DEBUG: Total de ocorrências para gerar para {master_bill.description}: {total_to_generate}")

    # Loop para gerar as ocorrências futuras
    for i in range(1, total_to_generate + 1):
        # Calcula a data da ocorrência atual (i-ésima ocorrência)
        occurrence_date_for_child = datetime.datetime.strptime(master_bill.recurring_start_date, '%Y-%m-%d').date()
        
        if i > 1: # Avança a data apenas a partir da segunda ocorrência
            if master_bill.recurring_frequency == 'monthly' or master_bill.recurring_frequency == 'installments':
                occurrence_date_for_child += relativedelta(months=i-1)
            elif master_bill.recurring_frequency == 'weekly':
                occurrence_date_for_child += relativedelta(weeks=i-1)
            elif master_bill.recurring_frequency == 'yearly':
                occurrence_date_for_child += relativedelta(years=i-1)

        # VERIFICAÇÃO DE DUPLICATAS MAIS PRECISA PARA EVITAR REGERAÇÃO APÓS DELETE
        existing_child_item = None
        if master_bill.type == 'expense': # Apenas Bills de despesa geram Bills filhas
            existing_child_item = Bill.query.filter_by(
                recurring_parent_id=master_bill.id,
                dueDate=occurrence_date_for_child.isoformat(),
                recurring_child_number=i, # IMPORTANTE: verifica se a CHILD_NUMBER já foi gerada para aquela data
                user_id=master_bill.user_id
            ).first()
        elif master_bill.type == 'income': # Apenas Bills de receita geram Transações filhas
            existing_child_item = Transaction.query.filter_by(
                description=master_bill.description.replace(' (Mestra)', ''), # Usa a descrição da mestra
                date=occurrence_date_for_child.isoformat(),
                type='income',
                user_id=master_bill.user_id
            ).first()


        if not existing_child_item:
            # Constrói a descrição para a Bill filha (Parcela X/Y ou apenas "Descrição Recorrente")
            if master_bill.recurring_frequency == 'installments' and master_bill.recurring_total_occurrences > 0:
                child_description = f"{master_bill.description.split(' (Mestra)')[0]} (Parcela {i}/{master_bill.recurring_total_occurrences})"
            else:
                child_description = master_bill.description.replace(' (Mestra)', '') # Remove "(Mestra)" da descrição

            # Define o status inicial da Bill filha
            new_child_bill_status = 'pending'
            if occurrence_date_for_child < TODAY_DATE: # Usa TODAY_DATE
                new_child_bill_status = 'overdue' # Marcar como atrasada

            if master_bill.type == 'expense': # Se a mestra é de despesa, gera uma Bill
                new_child_bill = Bill(
                    description=child_description,
                    amount=master_bill.amount,
                    dueDate=occurrence_date_for_child.isoformat(),
                    status=new_child_bill_status, # Status definido aqui
                    user_id=master_bill.user_id,
                    recurring_parent_id=master_bill.id,      # Link para a Bill mestra
                    recurring_child_number=i,                # Número da ocorrência gerada
                    is_master_recurring_bill=False, # A Bill filha NÃO é mestra
                    recurring_frequency=None, # Não aplicável para filhas
                    recurring_start_date=None, # Não aplicável para filhas
                    recurring_next_due_date=None, # Não aplicável para filhas
                    recurring_total_occurrences=0, # Não aplicável para filhas
                    recurring_installments_generated=0, # Não aplicável para filhas
                    is_active_recurring=False, # Bills filhas não estão ativas para gerar mais
                    type='expense' # A Bill gerada é de despesa
                )
                db.session.add(new_child_bill)
                generated_count_for_master += 1
                print(f"    Gerada Bill filha: {child_description} em {occurrence_date_for_child.isoformat()} com status {new_child_bill_status}") # Debug
            
            # REMOVIDO: Lógica para gerar Transaction de income a partir de Bill mestra de income
            # elif master_bill.type == 'income':
            #     new_generated_transaction = Transaction(
            #         description=child_description,
            #         amount=master_bill.amount,
            #         date=occurrence_date_for_child.isoformat(),
            #         type='income',
            #         user_id=master_bill.user_id,
            #         category_id=master_bill.category_id
            #     )
            #     db.session.add(new_generated_transaction)
            #     generated_count_for_master += 1
            #     print(f"    Gerada Transaction filha (Receita): {child_description} em {occurrence_date_for_child.isoformat()}")

        else:
            print(f"    Bill/Transaction filha já existe para {master_bill.description}, ocorrência {i} em {occurrence_date_for_child.isoformat()}, pulando.") # Debug

    # Atualiza o contador de ocorrências geradas na Bill mestra
    master_bill.recurring_installments_generated = generated_count_for_master
    
    # Recalcula a data de "next_due_date" da mestra para o FINAL do ciclo de geração em massa
    final_next_due_date_after_bulk_gen = datetime.datetime.strptime(master_bill.recurring_start_date, '%Y-%m-%d').date()
    if master_bill.recurring_total_occurrences > 0: # Se tem um total fixo, avança por esse total
        if master_bill.recurring_frequency == 'monthly' or master_bill.recurring_frequency == 'installments':
            final_next_due_date_after_bulk_gen += relativedelta(months=master_bill.recurring_total_occurrences)
        elif master_bill.recurring_frequency == 'weekly':
            final_next_due_date_after_bulk_gen += relativedelta(weeks=master_bill.recurring_total_occurrences)
        elif master_bill.recurring_frequency == 'yearly':
            final_next_due_date_after_bulk_gen += relativedelta(years=master_bill.recurring_total_occurrences)
    else: # Para recorrências indefinidas (total_occurrences == 0), avança para a data de hoje + 1 período
        if master_bill.recurring_frequency == 'monthly' or master_bill.recurring_frequency == 'installments':
            final_next_due_date_after_bulk_gen = TODAY_DATE + relativedelta(months=1) # Usa TODAY_DATE
        elif master_bill.recurring_frequency == 'weekly':
            final_next_due_date_after_bulk_gen = TODAY_DATE + relativedelta(weeks=1) # Usa TODAY_DATE
        elif master_bill.recurring_frequency == 'yearly':
            final_next_due_date_after_bulk_gen = TODAY_DATE + relativedelta(years=1) # Usa TODAY_DATE
        
        # Garante que a data não retroceda para indefinidos (se a calculated_date for anterior à start_date)
        if final_next_due_date_after_bulk_gen < datetime.datetime.strptime(master_bill.recurring_start_date, '%Y-%m-%d').date(): # Corrigido o nome da variável aqui
            final_next_due_date_after_bulk_gen = datetime.datetime.strptime(master_bill.recurring_start_date, '%Y-%m-%d').date() + relativedelta(months=1) # Ex: sempre um mês a frente se já no futuro

    master_bill.recurring_next_due_date = final_next_due_date_after_bulk_gen.isoformat()
    print(f"DEBUG: Próximo vencimento da semente '{master_bill.description}' atualizado para o fim da geração em massa: {master_bill.recurring_next_due_date}")

    # Desativa a mestra se o total de ocorrências fixas foi atingido
    if master_bill.recurring_total_occurrences and master_bill.recurring_installments_generated >= master_bill.recurring_total_occurrences:
        master_bill.is_active_recurring = False
        print(f"DEBUG: Master Bill '{master_bill.description}' desativada: todas as {master_bill.recurring_total_occurrences} ocorrências foram geradas.")
    else:
        # Se is_active_recurring é True, mantém True, se false (porque era passado), mantém false
        # Não muda a ativação a menos que atinja o total
        print(f"DEBUG: Master Bill '{master_bill.description}' ainda ativa (indefinida ou não atingiu total).")

    db.session.add(master_bill) # Adiciona a Bill mestra atualizada para a sessão
    db.session.commit() # Comita todas as alterações deste lote de processamento

# FUNÇÃO PRINCIPAL QUE É CHAMADA NA ROTA / E /PAY_BILL
def process_recurring_bills_on_access(user_id):
    # Buscar apenas Bills que são a "semente" da recorrência e ainda estão ativas para gerar novas ocorrências
    # E cuja recurring_next_due_date é <= TODAY_DATE
    recurring_seed_bills_to_process = Bill.query.filter(
        Bill.user_id == user_id,
        Bill.is_master_recurring_bill == True,
        Bill.is_active_recurring == True,
        db.cast(Bill.recurring_next_due_date, db.Date) <= TODAY_DATE # Apenas as mestras que estão "vencidas"
    ).all()
    
    print(f"\n--- process_recurring_bills_on_access chamada. Processando {len(recurring_seed_bills_to_process)} Bills mestras recorrentes devidas ---")

    for bill_seed in recurring_seed_bills_to_process:
        print(f"    Acionando geração em massa para mestra '{bill_seed.description}' (ID: {bill_seed.id}) por estar vencida.")
        _generate_future_recurring_bills(bill_seed)
        
    # Mensagens flash já são emitidas por _generate_future_recurring_bills.
    # Não é necessário um flash message consolidado aqui.

def add_bill_db(description, amount, due_date, user_id, 
                is_recurring=False, recurring_frequency=None, recurring_total_occurrences=0, bill_type='expense', category_id=None): # Adicionado bill_type e category_id
    
    # Validação para total de ocorrências
    if is_recurring and recurring_frequency == 'installments' and (recurring_total_occurrences is None or recurring_total_occurrences < 1):
        recurring_total_occurrences = 1 
    elif not is_recurring or recurring_frequency != 'installments':
        recurring_total_occurrences = 0 

    new_bill = Bill(
        description=description,
        amount=float(amount),
        dueDate=due_date,
        status='pending',
        user_id=user_id,
        is_master_recurring_bill=is_recurring, # Usa o novo campo
        recurring_frequency=recurring_frequency if is_recurring else None,
        recurring_start_date=due_date if is_recurring else None, # Data de início é a primeira due_date
        recurring_next_due_date=due_date if is_recurring else None, # Próxima a ser gerada é a própria dueDate
        recurring_total_occurrences=recurring_total_occurrences,
        recurring_installments_generated=0, # Inicia com 0 geradas
        is_active_recurring=is_recurring, # Ativa se for recorrente
        type=bill_type, # Salva o tipo da Bill
        category_id=category_id # Salva a categoria para a Bill (especialmente se for receita recorrente)
    )
    db.session.add(new_bill)
    db.session.commit() # Comita imediatamente para obter o ID da new_bill (essencial para recurring_parent_id)
    
    # Chama a geração em massa APENAS se esta é uma Bill mestra e está ativa para gerar
    if new_bill.is_master_recurring_bill and new_bill.is_active_recurring:
        _generate_future_recurring_bills(new_bill) # Chama a função para gerar as futuras ocorrências

def pay_bill_db(bill_id, user_id):
    bill = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if bill:
        bill.status = 'paid'
        db.session.add(bill)
        
        # CATEGORIA PARA O PAGAMENTO (SEMPE DESPESA)
        fixed_bills_category = Category.query.filter_by(name='Contas Fixas', type='expense').first()
        category_id_for_payment = fixed_bills_category.id if fixed_bills_category else None

        # Adiciona a transação comum correspondente ao pagamento (SEMPE DESPESA)
        # GERA UMA DESCRIÇÃO DE TRANSAÇÃO MAIS ESPECÍFICA PARA EVITAR DUPLICATAS NA TABELA DE TRANSAÇÕES
        # Isso garante que mesmo que se pague a mesma Bill mais de uma vez no mesmo dia,
        # cada pagamento gere uma transação única.
        # Usa o ID da Bill e a data atual para criar uma descrição única para CADA PAGAMENTO.
        payment_transaction_description = f"Pagamento: {bill.description} (Ref. Bill ID: {bill.id} - {datetime.date.today().isoformat()})"
        
        # Procura por uma transação de pagamento com a MESMA DESCRIÇÃO EXATA
        # Isso significa que, se você pagar o "Aluguel" (ID 1) no dia 27/06, e depois pagar o "Aluguel" (ID 2) no mesmo dia,
        # ambas serão registradas. Se tentar pagar a mesma Bill (ID 1) duas vezes no mesmo dia, a segunda será ignorada.
        existing_payment_transaction = Transaction.query.filter_by(
            description=payment_transaction_description, # Usar a descrição única para filtro
            date=datetime.date.today().isoformat(),
            type='expense',
            user_id=user_id
        ).first()

        if not existing_payment_transaction:
            new_payment_transaction = Transaction(
                description=payment_transaction_description, # Usar a descrição única
                amount=bill.amount,
                date=datetime.date.today().isoformat(),
                type='expense', # Pagamento é sempre uma despesa
                user_id=user_id,
                category_id=category_id_for_payment
            )
            db.session.add(new_payment_transaction)
            print(f"DEBUG: Transação de pagamento criada para '{payment_transaction_description}'.")
        else:
            print(f"DEBUG: Transação de pagamento para '{payment_transaction_description}' já existe. Pulando a criação.")
        
        # A Bill paga pode ser uma Bill mestra ou uma Bill filha gerada
        # Em ambos os casos, queremos garantir que a Bill mestra associada
        # tenha sua próxima ocorrência gerada (ou que o processo se encerre).
        
        master_bill_to_process = None
        if bill.is_master_recurring_bill: # Se a Bill paga é a mestra em si
            master_bill_to_process = bill
            print(f"DEBUG: Pagamento da Bill Mestra recorrente '{bill.description}'. Acionando _generate_future_recurring_bills.")
        elif bill.recurring_parent_id: # Se a Bill paga é uma filha de uma mestra
            master_bill_to_process = Bill.query.filter_by(
                id=bill.recurring_parent_id, # Link para a Bill mestra
                user_id=user_id,
                is_master_recurring_bill=True
            ).first()
            if master_bill_to_process:
                print(f"DEBUG: Pagamento de Bill filha '{bill.description}'. Acionando _generate_future_recurring_bills da mestra '{master_bill_to_process.description}'.")
            else:
                print(f"DEBUG: Bill filha paga, mas mestra recorrente não encontrada ou inativa: {bill.recurring_parent_id}")

        if master_bill_to_process and master_bill_to_process.is_active_recurring:
            _generate_future_recurring_bills(master_bill_to_process)
        elif master_bill_to_process and not master_bill_to_process.is_active_recurring:
            print(f"DEBUG: Mestra '{master_bill_to_process.description}' está inativa. Nenhuma nova geração.")
        
        db.session.commit() # Comita todas as alterações (bill.status, e a nova transação se criada)
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
        # Logic to reverse budget current_spent for expenses
        if transaction.type == 'expense' and transaction.category_id:
            transaction_month_year = datetime.datetime.strptime(transaction.date, '%Y-%m-%d').strftime('%Y-%m')
            budget = Budget.query.filter_by(
                user_id=user_id,
                category_id=transaction.category_id,
                month_year=transaction_month_year
            ).first()
            if budget:
                budget.current_spent -= transaction.amount
                db.session.add(budget)
                print(f"DEBUG: Budget for category {transaction.category_id} updated after deletion. New spent: {budget.current_spent}")
        
        db.session.delete(transaction)
        db.session.commit()
        return True
    return False

# MODIFICADA: delete_bill_db para lidar com recorrências mestras
def delete_bill_db(bill_id, user_id):
    bill = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if bill:
        print(f"DEBUG: Tentando excluir Bill ID: {bill.id}, Descrição: '{bill.description}', É Mestra Recorrente: {bill.is_master_recurring_bill}, Parent ID: {bill.recurring_parent_id}")

        if bill.is_master_recurring_bill:
            # Se é uma Bill mestra, exclua todas as suas filhas e depois a própria mestra.
            child_bills = Bill.query.filter_by(recurring_parent_id=bill.id, user_id=user_id).all()
            for child_bill in child_bills:
                db.session.delete(child_bill)
                print(f"DEBUG: Excluindo Bill filha (do mestre): ID {child_bill.id}, Descrição: '{child_bill.description}'")
            print(f"DEBUG: Excluídas {len(child_bills)} contas filhas para a mestra '{bill.description}'.")
            
            db.session.delete(bill) # Exclui a Bill mestra em si
            db.session.commit()
            print(f"DEBUG: Conta mestra '{bill.description}' (ID: {bill.id}) e suas filhas excluídas e commit realizado.")
            return True

        elif bill.recurring_parent_id:
            # Se é uma Bill filha, o usuário provavelmente quer cancelar a série inteira.
            # Encontre a Bill mestra e desative-a, depois exclua todas as suas filhas (incluindo a que está sendo excluída).
            master_bill = Bill.query.filter_by(id=bill.recurring_parent_id, user_id=user_id, is_master_recurring_bill=True).first()
            if master_bill:
                print(f"DEBUG: Bill filha '{bill.description}' (ID: {bill.id}) está sendo excluída. Tentando cancelar a série mestra '{master_bill.description}'.")
                
                master_bill.is_active_recurring = False # Desativa a geração futura
                master_bill.recurring_frequency = None # Limpa os campos de recorrência
                master_bill.recurring_start_date = None
                master_bill.recurring_next_due_date = None
                master_bill.recurring_total_occurrences = 0
                master_bill.recurring_installments_generated = 0
                db.session.add(master_bill) # Salva as alterações na Bill mestra

                # Exclui todas as filhas associadas a esta Bill mestra (incluindo a que acabou de ser clicada)
                all_children_of_master = Bill.query.filter_by(recurring_parent_id=master_bill.id, user_id=user_id).all()
                for child in all_children_of_master:
                    db.session.delete(child)
                    print(f"DEBUG: Excluindo outra Bill filha (do mestre): ID {child.id}, Descrição: '{child.description}'")
                
                db.session.commit()
                print(f"DEBUG: Série recorrente da mestra '{master_bill.description}' (ID: {master_bill.id}) cancelada e todas as suas filhas excluídas.")
                return True
            else:
                print(f"DEBUG: Bill filha '{bill.description}' (ID: {bill.id}) excluída, mas a mestra recorrente (ID: {bill.recurring_parent_id}) não foi encontrada ou não é mestra.")
                # Se a mestra não foi encontrada, apenas exclua a Bill filha em si.
                db.session.delete(bill)
                db.session.commit()
                return True # Ainda considera um sucesso por excluir a filha
        else:
            # É uma Bill regular, não recorrente. Apenas a exclua.
            db.session.delete(bill)
            db.session.commit()
            print(f"DEBUG: Bill não recorrente '{bill.description}' (ID: {bill.id}) excluída e commit realizado.")
            return True
    
    print(f"DEBUG: Bill ID {bill_id} não encontrada ou não pertence ao usuário {user_id}.")
    return False

def edit_transaction_db(transaction_id, description, amount, date, type, user_id, category_id=None):
    transaction = Transaction.query.filter_by(id=transaction_id, user_id=user_id).first()
    if transaction:
        old_amount = transaction.amount
        old_type = transaction.type
        old_category_id = transaction.category_id
        old_date = transaction.date

        transaction.description = description
        transaction.amount = float(amount)
        transaction.date = date
        transaction.type = type
        transaction.category_id = category_id

        # Logic to update budget current_spent for expenses (considering changes)
        old_month_year = datetime.datetime.strptime(old_date, '%Y-%m-%d').strftime('%Y-%m')
        new_month_year = datetime.datetime.strptime(date, '%Y-%m-%d').strftime('%Y-%m')

        # If it was an expense and had a category, reverse its impact
        if old_type == 'expense' and old_category_id:
            old_budget = Budget.query.filter_by(
                user_id=user_id,
                category_id=old_category_id,
                month_year=old_month_year
            ).first()
            if old_budget:
                old_budget.current_spent -= old_amount
                db.session.add(old_budget)
                print(f"DEBUG: Reversed old budget for category {old_category_id}. New spent: {old_budget.current_spent}")

        # If the new transaction is an expense and has a category, apply its impact
        if type == 'expense' and category_id:
            new_budget = Budget.query.filter_by(
                user_id=user_id,
                category_id=category_id,
                month_year=new_month_year
            ).first()
            if new_budget:
                new_budget.current_spent += float(amount)
                db.session.add(new_budget)
                print(f"DEBUG: Applied new budget for category {category_id}. New spent: {new_budget.current_spent}")
            else:
                print(f"DEBUG: No active budget found for category {category_id} in {new_month_year} for user {user_id}. New transaction impact not applied to budget.")
        
        db.session.commit()
        return True
    return False

# MODIFICADA: edit_bill_db para editar campos de recorrência
def edit_bill_db(bill_id, description, amount, dueDate, user_id, 
                    is_recurring=False, recurring_frequency=None, recurring_total_occurrences=0, is_active_recurring=False, bill_type='expense', category_id=None): # Adicionado category_id
    bill = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if bill:
        bill.description = description
        bill.amount = float(amount)
        bill.dueDate = dueDate
        bill.type = bill_type # Atualiza o tipo da Bill
        bill.category_id = category_id # Salva a categoria para a Bill (especialmente se for receita recorrente)

        # Se esta Bill é uma Bill mestra (is_master_recurring_bill == True)
        if bill.is_master_recurring_bill: # Garante que só edita recorrência se a Bill é mestra
            # Capturar o estado antigo para decidir se precisa regenerar
            old_is_active_recurring = bill.is_active_recurring
            old_frequency = bill.recurring_frequency
            old_total_occurrences = bill.recurring_total_occurrences

            bill.is_master_recurring_bill = is_recurring # Atualiza se deixou de ser mestra
            bill.is_active_recurring = is_active_recurring # Controla se a recorrência mestra está ativa
            bill.recurring_frequency = recurring_frequency if is_recurring else None
            bill.recurring_total_occurrences = recurring_total_occurrences if is_recurring and recurring_frequency == 'installments' else 0
            
            # Re-gerar Bills futuras se:
            # 1. A mestra foi ATIVADA (ou reativada)
            # 2. OU a frequência mudou (e ainda é recorrente)
            # 3. OU o total de ocorrências mudou (e ainda é recorrente)
            if is_recurring and is_active_recurring and \
               (not old_is_active_recurring or old_frequency != recurring_frequency or old_total_occurrences != recurring_total_occurrences):
                print(f"DEBUG: Editando Bill mestra '{bill.description}'. Parâmetros de recorrência alterados ou reativada. Regenerando futuras ocorrências.")
                _generate_future_recurring_bills(bill) # Esta função cuida da deleção e geração

            elif not is_recurring and old_is_active_recurring: # Se desativou a recorrência (agora não é mais 'is_recurring')
                bill.is_active_recurring = False
                # Limpar campos de recorrência e deletar futuras ocorrências
                bill.recurring_frequency = None
                bill.recurring_next_due_date = None # Limpa próxima data gerada
                bill.recurring_total_occurrences = 0
                bill.recurring_installments_generated = 0
                # Deleta futuras ocorrências pendentes
                Bill.query.filter_by(recurring_parent_id=bill.id, user_id=user_id, status='pending').delete()
                print(f"DEBUG: Master Bill '{bill.description}' desativada, futuras Bills filhas deletadas.")
            elif is_recurring and not is_active_recurring and old_is_active_recurring: # Se está marcada como recorrente, mas foi desativada manualmente (is_recurring continua True, mas is_active_recurring vira False)
                print(f"DEBUG: Master Bill '{bill.description}' marcada como inativa manualmente.")
                # Apenas desativar, mas não apagar as filhas se o usuário quiser reativar depois
        
        else: # Se não é uma Bill mestra (ou não se tornou uma)
            bill.is_master_recurring_bill = False
            bill.is_active_recurring = False
            bill.recurring_frequency = None
            bill.recurring_start_date = None
            bill.recurring_next_due_date = None
            bill.recurring_total_occurrences = 0
            bill.recurring_installments_generated = 0
            # Nenhuma ação sobre recurring_parent_id, pois ele linka para a mestra

        db.session.commit()
        return True
    return False

# MODIFIED: get_dashboard_data_db to show monthly values for income, expenses, and pending bills.
def get_dashboard_data_db(user_id):
    current_month_start = datetime.date.today().replace(day=1).isoformat()
    next_month_start = (datetime.date.today().replace(day=1) + relativedelta(months=1)).isoformat()

    # Calculate total balance (accumulated)
    all_transactions = Transaction.query.filter_by(user_id=user_id).all()
    total_income_overall = sum(t.amount for t in all_transactions if t.type == 'income')
    total_expenses_overall = sum(t.amount for t in all_transactions if t.type == 'expense')
    balance = total_income_overall - total_expenses_overall

    # Calculate monthly income
    monthly_income = sum(
        t.amount for t in all_transactions
        if t.type == 'income' and t.date >= current_month_start and t.date < next_month_start
    )

    # Calculate monthly expenses
    monthly_expenses = sum(
        t.amount for t in all_transactions
        if t.type == 'expense' and t.date >= current_month_start and t.date < next_month_start
    )

    # Calculate total pending bills for the current month (or future months if due date is later than today)
    # We should consider all pending bills that have a due date in the current month or are overdue from previous months
    # but for "current month's pending bills", let's consider bills due in the current month or overdue.
    all_pending_bills = Bill.query.filter(
        Bill.user_id == user_id,
        Bill.status == 'pending',
        Bill.is_master_recurring_bill == False # Exclude master bills from pending bills list
    ).all()

    # Filter pending bills to only include those due in the current month or overdue from previous months
    monthly_pending_bills = [
        b for b in all_pending_bills
        if b.dueDate >= current_month_start and b.dueDate < next_month_start
        or (b.dueDate < current_month_start and b.status == 'pending') # Include overdue bills
    ]
    total_pending_bills_amount_monthly = sum(b.amount for b in monthly_pending_bills)
    
    return {
        'balance': balance,
        'totalIncome': monthly_income, # Now displays monthly income
        'totalExpenses': monthly_expenses, # Now displays monthly expenses
        'totalPendingBills': total_pending_bills_amount_monthly, # Now displays monthly pending bills
        'pendingBillsList': all_pending_bills # This list remains comprehensive for display purposes
    }

def generate_text_with_gemini(prompt_text):
    try:
        model = genai.GenerativeModel('gemini-2.0-flash')
        response = model.generate_content(prompt_text)
        return response.text
    except Exception as e:
        print(f"Erro ao chamar Gemini API: {e}")
        return "Não foi possível gerar uma sugestão/resumo no momento."

# --- NOVAS FUNÇÕES PARA ORÇAMENTOS E METAS ---
def add_budget_db(user_id, category_id, budget_amount, month_year):
    # Check for existing budget for the same category and month_year
    existing_budget = Budget.query.filter_by(
        user_id=user_id,
        category_id=category_id,
        month_year=month_year
    ).first()
    if existing_budget:
        # Optionally, update existing budget or raise an error
        print("Budget already exists for this category and month. Updating existing budget.")
        existing_budget.budget_amount = budget_amount
        db.session.add(existing_budget)
    else:
        new_budget = Budget(
            user_id=user_id,
            category_id=category_id,
            budget_amount=float(budget_amount),
            month_year=month_year
        )
        db.session.add(new_budget)
    db.session.commit()
    return True

def edit_budget_db(budget_id, user_id, budget_amount=None):
    budget = Budget.query.filter_by(id=budget_id, user_id=user_id).first()
    if budget:
        if budget_amount is not None:
            budget.budget_amount = float(budget_amount)
        db.session.commit()
        return True
    return False

def delete_budget_db(budget_id, user_id):
    budget = Budget.query.filter_by(id=budget_id, user_id=user_id).first()
    if budget:
        db.session.delete(budget)
        db.session.commit()
        return True
    return False

def add_goal_db(user_id, name, target_amount, due_date=None):
    new_goal = Goal(
        user_id=user_id,
        name=name,
        target_amount=float(target_amount),
        due_date=due_date if due_date else None,
        status='in_progress'
    )
    db.session.add(new_goal)
    db.session.commit()
    return True

def edit_goal_db(goal_id, user_id, name=None, target_amount=None, current_amount=None, due_date=None, status=None):
    goal = Goal.query.filter_by(id=goal_id, user_id=user_id).first()
    if goal:
        if name:
            goal.name = name
        if target_amount is not None:
            goal.target_amount = float(target_amount)
        if current_amount is not None:
            goal.current_amount = float(current_amount)
        if due_date:
            goal.due_date = due_date
        if status:
            goal.status = status
        db.session.commit()
        return True
    return False

def delete_goal_db(goal_id, user_id):
    goal = Goal.query.filter_by(id=goal_id, user_id=user_id).first()
    if goal:
        db.session.delete(goal)
        db.session.commit()
        return True
    return False

def contribute_to_goal_db(goal_id, user_id, amount):
    goal = Goal.query.filter_by(id=goal_id, user_id=user_id).first()
    if goal:
        goal.current_amount += float(amount)
        if goal.current_amount >= goal.target_amount:
            goal.status = 'achieved'
        db.session.add(goal)
        # Create a transaction for the contribution
        salario_category = Category.query.filter_by(name='Salário', type='income').first() # Or a specific 'Savings' category
        poupanca_metas_category = Category.query.filter_by(name='Poupança para Metas', type='expense').first()
        
        category_id = poupanca_metas_category.id if poupanca_metas_category else None
        
        # Add a record to transactions for visibility
        new_transaction = Transaction(
            description=f"Contribuição para Meta: {goal.name}",
            amount=float(amount),
            date=datetime.date.today().isoformat(),
            type='expense', # It's an expense from your available cash, moving to savings/goal
            user_id=user_id,
            category_id=category_id # Could be a 'Savings' category
        )
        db.session.add(new_transaction)
        db.session.commit()
        return True
    return False

# --- Rotas da Aplicação ---

@app.route('/')
@login_required
def index():
    # Processa as transações recorrentes do usuário ANTES de carregar a página
    # para que as contas/transações geradas apareçam no dashboard.
    process_recurring_bills_on_access(current_user.id)
    
    dashboard_data = get_dashboard_data_db(current_user.id)
    
    transactions_query_obj = Transaction.query.filter_by(user_id=current_user.id) 

    transaction_type_filter = request.args.get('transaction_type')
    if transaction_type_filter and transaction_type_filter in ['income', 'expense']:
        transactions_query_obj = transactions_query_obj.filter_by(type=transaction_type_filter)

    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    if start_date:
        transactions_query_obj = transactions_query_obj.filter(db.cast(Transaction.date, db.Date) >= db.cast(start_date, db.Date))
    if end_date:
        transactions_query_obj = transactions_query_obj.filter(db.cast(Transaction.date, db.Date) <= db.cast(end_date, db.Date))

    category_filter_id = request.args.get('category_filter', type=int)
    if category_filter_id:
        transactions_query_obj = transactions_query_obj.filter_by(category_id=category_filter_id)

    sort_by_transactions = request.args.get('sort_by_transactions', 'date')
    order_transactions = request.args.get('order_transactions', 'desc')

    if sort_by_transactions == 'date':
        if order_transactions == 'asc':
            transactions_query_obj = transactions_query_obj.order_by(Transaction.date.asc())
        else:
            transactions_query_obj = transactions_query_obj.order_by(Transaction.date.desc())
    elif sort_by_transactions == 'amount':
        if order_transactions == 'asc':
            transactions_query_obj = transactions_query_obj.order_by(Transaction.amount.asc())
        else:
            transactions_query_obj = transactions_query_obj.order_by(Transaction.amount.desc())
            
    all_transactions = transactions_query_obj.all() # Execute a consulta aqui
    
    income_transactions = [t for t in all_transactions if t.type == 'income']
    expense_transactions = [t for t in all_transactions if t.type == 'expense']


    # Bills a serem exibidas: apenas as Bills que NÃO são mestras de recorrência
    # (ou seja, são Bills criadas manualmente ou Bills filhas geradas).
    bills_query_obj = Bill.query.filter( 
        Bill.user_id == current_user.id,
        Bill.is_master_recurring_bill == False # Exclui as Bills mestras da exibição
    )

    bill_status_filter = request.args.get('bill_status')
    if bill_status_filter and bill_status_filter in ['pending', 'paid', 'overdue']:
        if bill_status_filter == 'overdue':
            today_str = datetime.date.today().isoformat()
            bills_query_obj = bills_query_obj.filter(Bill.dueDate < today_str, Bill.status == 'pending')
        else:
            bills_query_obj = bills_query_obj.filter_by(status=bill_status_filter)
    elif not bill_status_filter:
            bills_query_obj = bills_query_obj.filter_by(status='pending')

    sort_by_bills = request.args.get('sort_by_bills', 'dueDate')
    order_bills = request.args.get('order_bills', 'asc')

    if sort_by_bills == 'dueDate':
        if order_bills == 'asc':
            bills_query_obj = bills_query_obj.order_by(Bill.dueDate.asc())
        else:
            bills_query_obj = bills_query_obj.order_by(Bill.dueDate.desc())
    elif sort_by_bills == 'amount':
        if order_bills == 'asc':
            bills_query_obj = bills_query_obj.order_by(Bill.amount.asc())
        else:
            bills_query_obj = bills_query_obj.order_by(Bill.amount.desc())
            
    filtered_bills = bills_query_obj.all() # Execute a consulta aqui

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
    
    is_recurring = request.form.get('is_recurring_bill') == 'on' # Checkbox retorna 'on' ou None
    recurring_frequency = request.form.get('recurring_frequency_bill')
    recurring_total_occurrences = request.form.get('recurring_total_occurrences_bill', type=int) # NOVO: Total de ocorrências
    bill_type = request.form['bill_type'] # Pega o tipo (expense/income) do formulário
    category_id = request.form.get('bill_category_id', type=int) # NOVO: Pega a categoria para a bill recorrente

    add_bill_db(description, amount, due_date, current_user.id, 
                is_recurring, recurring_frequency, recurring_total_occurrences, bill_type, category_id) # Passa o total de ocorrências e category_id
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
            'status': bill.status,
            'is_master_recurring_bill': bill.is_master_recurring_bill,
            'recurring_parent_id': bill.recurring_parent_id,
            'recurring_child_number': bill.recurring_child_number,
            'recurring_frequency': bill.recurring_frequency,
            'recurring_start_date': bill.recurring_start_date,
            'recurring_next_due_date': bill.recurring_next_due_date,
            'recurring_total_occurrences': bill.recurring_total_occurrences,
            'recurring_installments_generated': bill.recurring_installments_generated,
            'is_active_recurring': bill.is_active_recurring,
            'type': bill.type,
            'category_id': bill.category_id # NOVO: Retorna o category_id
        })
    return jsonify({'error': 'Conta não encontrada ou não pertence a este usuário'}), 404

@app.route('/edit_bill/<int:bill_id>', methods=['POST'])
@login_required
def handle_edit_bill(bill_id):
    description = request.form['edit_bill_description']
    amount = request.form['edit_bill_amount']
    due_date = request.form['edit_bill_dueDate']
    
    is_recurring = request.form.get('edit_is_recurring_bill') == 'on'
    recurring_frequency = request.form.get('edit_recurring_frequency_bill')
    recurring_total_occurrences = request.form.get('edit_recurring_total_occurrences_bill', type=int)
    is_active_recurring = request.form.get('edit_is_active_recurring_bill') == 'on'
    bill_type = request.form['edit_bill_type']
    category_id = request.form.get('edit_bill_category_id', type=int) # NOVO: Pega a categoria para a bill recorrente

    if edit_bill_db(bill_id, description, amount, due_date, current_user.id,
                    is_recurring, recurring_frequency, recurring_total_occurrences, is_active_recurring, bill_type, category_id): # Passa category_id
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
    budgets_data = data.get('budgets_data', [])
    goals_data = data.get('goals_data', [])
    
    if not monthly_summary:
        return jsonify({'error': 'Dados de resumo não fornecidos.'}), 400

    prompt_parts = [
        f"Com base nos seguintes dados financeiros de um mês específico:",
        f"Receita Total: R${monthly_summary['income']:.2f},",
        f"Despesa Total: R${monthly_summary['expenses']:.2f},",
        f"Saldo Mensal: R${monthly_summary['balance']:.2f}."
    ]

    if monthly_summary.get('transactions_details'):
        # Limit transaction details to avoid prompt overflow, or summarize them
        limited_transactions = monthly_summary['transactions_details'][:5] # Top 5 transactions
        prompt_parts.append(f"Principais transações: {limited_transactions}.")
    
    if budgets_data:
        budget_summary = "Detalhes dos orçamentos (categoria, orçado, gasto): "
        for b in budgets_data:
            budget_summary += f"{b['category_name']}: R${b['budget_amount']:.2f} (gasto: R${b['current_spent']:.2f}). "
        prompt_parts.append(budget_summary)

    if goals_data:
        goal_summary = "Detalhes das metas (nome, alvo, atual, status): "
        for g in goals_data:
            goal_summary += f"'{g['name']}': R${g['target_amount']:.2f} (atual: R${g['current_amount']:.2f}, status: {g['status']}). "
        prompt_parts.append(goal_summary)

    prompt_parts.append(
        f"Forneça um breve insight ou conselho financeiro pessoal para o usuário. "
        f"Concentre-se em pontos fortes, áreas para melhoria ou tendências. "
        f"Use uma linguagem amigável e direta em português. "
        f"Seja conciso, com no máximo 150 palavras (pode exceder ligeiramente se necessário para clareza). "
        f"Não inclua 'Olá!' ou saudações, vá direto ao ponto."
    )

    prompt_text = " ".join(prompt_parts)
    print(f"DEBUG: Prompt enviado ao Gemini: {prompt_text}") # Debugging the prompt
    ai_text = generate_text_with_gemini(prompt_text)
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

# --- NOVAS ROTAS PARA ORÇAMENTOS ---
@app.route('/budgets', methods=['GET'])
@login_required
def budgets_page():
    # Fetch budgets for the current user, optionally filtered by month/year
    # Use current date to determine default month_year for display
    default_month_year = datetime.date.today().strftime('%Y-%m')
    month_year_filter = request.args.get('month_year', default_month_year)
    
    budgets = Budget.query.filter_by(user_id=current_user.id, month_year=month_year_filter).all()
    
    # Get all expense categories for the budget creation form
    expense_categories = Category.query.filter_by(type='expense').all()
    
    # Calculate current month's expenses per category for the actual spending part
    # This ensures that even if a budget has 0 current_spent in the DB, it reflects actual spending
    current_month_start_date_obj = datetime.datetime.strptime(month_year_filter, '%Y-%m').date().replace(day=1)
    next_month_start_date_obj = (current_month_start_date_obj + relativedelta(months=1))
    
    transactions_in_month = Transaction.query.filter(
        Transaction.user_id == current_user.id,
        db.cast(Transaction.date, db.Date) >= current_month_start_date_obj,
        db.cast(Transaction.date, db.Date) < next_month_start_date_obj,
        Transaction.type == 'expense'
    ).all()
    
    current_month_expenses_by_category = {}
    for t in transactions_in_month:
        if t.category:
            current_month_expenses_by_category[t.category.id] = current_month_expenses_by_category.get(t.category.id, 0) + t.amount

    # Update current_spent for budgets based on actual transactions
    for budget in budgets:
        budget.current_spent = current_month_expenses_by_category.get(budget.category_id, 0)
        # No commit here, as it's just for display. The current_spent is updated
        # permanently in add/edit/delete transaction functions.

    return render_template(
        'budgets.html',
        budgets=budgets,
        expense_categories=expense_categories,
        current_month_year=month_year_filter
    )

@app.route('/add_budget', methods=['POST'])
@login_required
def handle_add_budget():
    category_id = request.form['category_id']
    budget_amount = request.form['budget_amount']
    month_year = request.form['month_year']

    if add_budget_db(current_user.id, category_id, budget_amount, month_year):
        flash('Orçamento adicionado/atualizado com sucesso!', 'success')
    else:
        flash('Erro ao adicionar/atualizar orçamento.', 'danger')
    return redirect(url_for('budgets_page', month_year=month_year)) # Redirect back to the same month

@app.route('/edit_budget/<int:budget_id>', methods=['POST'])
@login_required
def handle_edit_budget(budget_id):
    budget_amount = request.form['budget_amount']
    if edit_budget_db(budget_id, current_user.id, budget_amount):
        flash('Orçamento atualizado com sucesso!', 'success')
    else:
        flash('Erro ao atualizar orçamento.', 'danger')
    return redirect(url_for('budgets_page'))

@app.route('/delete_budget/<int:budget_id>', methods=['POST'])
@login_required
def handle_delete_budget(budget_id):
    if delete_budget_db(budget_id, current_user.id):
        flash('Orçamento excluído com sucesso!', 'info')
    else:
        flash('Erro ao excluir orçamento.', 'danger')
    return redirect(url_for('budgets_page'))

# --- NOVAS ROTAS PARA METAS ---
@app.route('/goals', methods=['GET'])
@login_required
def goals_page():
    goals = Goal.query.filter_by(user_id=current_user.id).all()
    return render_template('goals.html', goals=goals)

@app.route('/add_goal', methods=['POST'])
@login_required
def handle_add_goal():
    name = request.form['name']
    target_amount = request.form['target_amount']
    due_date = request.form['due_date'] if request.form['due_date'] else None

    if add_goal_db(current_user.id, name, target_amount, due_date):
        flash('Meta adicionada com sucesso!', 'success')
    else:
        flash('Erro ao adicionar meta.', 'danger')
    return redirect(url_for('goals_page'))

@app.route('/edit_goal/<int:goal_id>', methods=['POST'])
@login_required
def handle_edit_goal(goal_id):
    name = request.form['name']
    target_amount = request.form['target_amount']
    current_amount = request.form['current_amount']
    due_date = request.form['due_date'] if request.form['due_date'] else None
    status = request.form['status']

    if edit_goal_db(goal_id, current_user.id, name, target_amount, current_amount, due_date, status):
        flash('Meta atualizada com sucesso!', 'success')
    else:
        flash('Erro ao atualizar meta.', 'danger')
    return redirect(url_for('goals_page'))

@app.route('/delete_goal/<int:goal_id>', methods=['POST'])
@login_required
def handle_delete_goal(goal_id):
    if delete_goal_db(goal_id, current_user.id):
        flash('Meta excluída com sucesso!', 'info')
    else:
        flash('Erro ao excluir meta.', 'danger')
    return redirect(url_for('goals_page'))

@app.route('/contribute_to_goal/<int:goal_id>', methods=['POST'])
@login_required
def handle_contribute_to_goal(goal_id):
    amount = request.form['amount']
    if contribute_to_goal_db(goal_id, current_user.id, amount):
        flash(f'Contribuição de R$ {amount} adicionada à meta!', 'success')
    else:
        flash('Erro ao contribuir para a meta.', 'danger')
    return redirect(url_for('goals_page'))


if __name__ == '__main__':
    with app.app_context():
        # APAGARA TODO O SEU BANCO DE DADOS A CADA INICIALIZAÇÃO!
        # Remova esta linha após a correção do esquema em produção.
        # Para evitar perda de dados em produção, use Flask-Migrate (Alembic) para gerenciar migrações de esquema.
        # db.drop_all()  
        db.create_all()

        # Imprime o caminho completo do arquivo do banco de dados para depuração
        print(f"DEBUG: Caminho do banco de dados: {app.config['SQLALCHEMY_DATABASE_URI']}")
        
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
            db.session.add(Category(name='Poupança para Metas', type='expense')) # Nova categoria para metas
            db.session.commit()
            print("Categorias padrão adicionadas.")

        if not User.query.filter_by(username='admin').first():
            admin_user = User(username='admin')
            admin_user.set_password('admin123')
            db.session.add(admin_user)
            db.session.commit()
            print("Usuário 'admin' criado com senha 'admin123'.")

        if User.query.first():
            first_user = User.query.first()
            contas_fixas_category = Category.query.filter_by(name='Contas Fixas', type='expense').first()
            alimentacao_category = Category.query.filter_by(name='Alimentação', type='expense').first()
            lazer_category = Category.query.filter_by(name='Lazer', type='expense').first()
            poupanca_metas_category = Category.query.filter_by(name='Poupança para Metas', type='expense').first()

            # Existing Bill examples (ensure they still work)
            if not Bill.query.filter_by(user_id=first_user.id, description='Aluguel Apartamento (Mestra)').first():
                db.session.add(Bill(
                    description='Aluguel Apartamento (Mestra)', amount=1500.00, dueDate='2024-01-05', status='pending',
                    user_id=first_user.id, is_master_recurring_bill=True, recurring_frequency='monthly',
                    recurring_start_date='2024-01-05', recurring_next_due_date='2024-01-05', recurring_total_occurrences=0,
                    recurring_installments_generated=0, is_active_recurring=True, type='expense',
                    category_id=contas_fixas_category.id if contas_fixas_category else None
                ))
            if not Bill.query.filter_by(user_id=first_user.id, description='Internet Fibra (Mestra)').first():
                db.session.add(Bill(
                    description='Internet Fibra (Mestra)', amount=99.90, dueDate='2024-01-10', status='pending',
                    user_id=first_user.id, is_master_recurring_bill=True, recurring_frequency='monthly',
                    recurring_start_date='2024-01-10', recurring_next_due_date='2024-01-10', recurring_total_occurrences=0,
                    recurring_installments_generated=0, is_active_recurring=True, type='expense',
                    category_id=contas_fixas_category.id if contas_fixas_category else None
                ))
            if not Bill.query.filter_by(user_id=first_user.id, description='Compra Parcelada Tênis (Mestra)').first():
                db.session.add(Bill(
                    description='Compra Parcelada Tênis (Mestra)', amount=100.00, dueDate='2024-01-01', status='pending',
                    user_id=first_user.id, is_master_recurring_bill=True, recurring_frequency='installments',
                    recurring_start_date='2024-01-01', recurring_next_due_date='2024-01-01', recurring_total_occurrences=5,
                    recurring_installments_generated=0, is_active_recurring=True, type='expense',
                    category_id=contas_fixas_category.id if contas_fixas_category else None
                ))

            # New: Example Budgets for current month (adjust month_year as needed)
            current_month_for_examples = datetime.date.today().strftime('%Y-%m') # E.g., '2025-07'
            
            if alimentacao_category and not Budget.query.filter_by(user_id=first_user.id, category_id=alimentacao_category.id, month_year=current_month_for_examples).first():
                db.session.add(Budget(user_id=first_user.id, category_id=alimentacao_category.id, budget_amount=500.00, month_year=current_month_for_examples))
            if lazer_category and not Budget.query.filter_by(user_id=first_user.id, category_id=lazer_category.id, month_year=current_month_for_examples).first():
                db.session.add(Budget(user_id=first_user.id, category_id=lazer_category.id, budget_amount=200.00, month_year=current_month_for_examples))
            if contas_fixas_category and not Budget.query.filter_by(user_id=first_user.id, category_id=contas_fixas_category.id, month_year=current_month_for_examples).first():
                db.session.add(Budget(user_id=first_user.id, category_id=contas_fixas_category.id, budget_amount=2000.00, month_year=current_month_for_examples))

            # New: Example Goals
            if not Goal.query.filter_by(user_id=first_user.id, name='Viagem para a Praia').first():
                db.session.add(Goal(user_id=first_user.id, name='Viagem para a Praia', target_amount=5000.00, current_amount=1200.00, due_date='2025-12-31'))
            if not Goal.query.filter_by(user_id=first_user.id, name='Fundo de Emergência').first():
                db.session.add(Goal(user_id=first_user.id, name='Fundo de Emergência', target_amount=3000.00, current_amount=800.00, due_date='2024-12-31'))
            
            db.session.commit()
            print("Contas e transações recorrentes mestras de exemplo, orçamentos e metas adicionados.")

    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)