from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

# Inicializa o objeto SQLAlchemy. Ele será associado ao app em app.py
db = SQLAlchemy()

class User(UserMixin, db.Model):
    """Modelo de Usuário para autenticação e relacionamento com outros dados."""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    profile_picture_url = db.Column(db.String(255), nullable=True, default='https://placehold.co/100x100/aabbcc/ffffff?text=PF')
    
    # Campos para recuperação de senha
    recovery_code = db.Column(db.String(6), nullable=True)
    recovery_code_expires_at = db.Column(db.DateTime, nullable=True)

    # Relacionamentos
    transactions = db.relationship('Transaction', backref='user', lazy=True, cascade='all, delete-orphan')
    bills = db.relationship('Bill', backref='user', lazy=True, cascade='all, delete-orphan')
    budgets = db.relationship('Budget', backref='user_budget_owner', lazy=True, cascade='all, delete-orphan')
    goals = db.relationship('Goal', backref='user_goal_owner', lazy=True, cascade='all, delete-orphan')
    categories = db.relationship('Category', backref='owner', lazy=True, cascade='all, delete-orphan')
    accounts = db.relationship('Account', backref='owner', lazy=True, cascade='all, delete-orphan')

    def set_password(self, password):
        """Define a senha do usuário, armazenando seu hash."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Verifica se a senha fornecida corresponde ao hash armazenado."""
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.email}>"

class Category(db.Model):
    """Modelo de Categoria para transações e orçamentos."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(10), nullable=False) # 'income' ou 'expense'
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    transactions = db.relationship('Transaction', backref='category', lazy=True)
    budgets = db.relationship('Budget', backref='category', lazy=True) 

    # Garante que um usuário não tenha duas categorias com o mesmo nome e tipo
    __table_args__ = (db.UniqueConstraint('name', 'type', 'user_id', name='_user_category_type_uc'),)

    def __repr__(self):
        return f"<Category {self.name} ({self.type})>"

class Account(db.Model):
    """Modelo de Conta Financeira (ex: Conta Corrente, Poupança, Dinheiro)."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    transactions = db.relationship('Transaction', backref='account', lazy=True)
    bills = db.relationship('Bill', backref='account_bill', lazy=True)

    # Garante que um usuário não tenha duas contas com o mesmo nome
    __table_args__ = (db.UniqueConstraint('name', 'user_id', name='_user_account_uc'),)

    def __repr__(self):
        return f"<Account {self.name} (Balance: {self.balance:.2f})>"

class Transaction(db.Model):
    """Modelo de Transação Financeira (receita ou despesa)."""
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.String(10), nullable=False) # Armazenar como String YYYY-MM-DD
    type = db.Column(db.String(10), nullable=False) # 'income' ou 'expense'
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'), nullable=True)
    goal_id = db.Column(db.Integer, db.ForeignKey('goal.id'), nullable=True) # Opcional: transação associada a uma meta

    def __repr__(self):
        return f"<Transaction {self.description} - {self.amount}>"

class Bill(db.Model):
    """Modelo de Conta a Pagar/Receber, incluindo recorrência."""
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    dueDate = db.Column(db.String(10), nullable=False) # Data de vencimento como string YYYY-MM-DD
    status = db.Column(db.String(10), nullable=False) # 'pending', 'paid', 'overdue'
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Campos para contas recorrentes (apenas para a "mestra")
    is_master_recurring_bill = db.Column(db.Boolean, default=False, nullable=False)
    recurring_parent_id = db.Column(db.Integer, db.ForeignKey('bill.id'), nullable=True) # Aponta para a mestra
    recurring_child_number = db.Column(db.Integer, nullable=True) # Número da ocorrência (ex: Parcela 1 de 12)
    
    recurring_frequency = db.Column(db.String(20), nullable=True) # 'monthly', 'weekly', 'yearly', 'installments'
    recurring_start_date = db.Column(db.String(10), nullable=True) # Data de início da recorrência
    recurring_next_due_date = db.Column(db.String(10), nullable=True) # Próxima data para gerar uma ocorrência
    recurring_total_occurrences = db.Column(db.Integer, nullable=True) # Total de parcelas/ocorrências (0 para indefinido)
    recurring_installments_generated = db.Column(db.Integer, nullable=True, default=0) # Quantas já foram geradas
    is_active_recurring = db.Column(db.Boolean, default=False, nullable=False) # Se a recorrência está ativa

    type = db.Column(db.String(10), nullable=False, default='expense') # 'expense' ou 'income'
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'), nullable=True) # Conta associada (para pagamento)
    
    # Relacionamento com a transação de pagamento (se for paga)
    payment_transaction_id = db.Column(db.Integer, db.ForeignKey('transaction.id'), nullable=True)
    payment_transaction = db.relationship('Transaction', foreign_keys=[payment_transaction_id], post_update=True)

    def __repr__(self):
        return f"<Bill {self.description} - {self.dueDate} - {self.status}>"

class Budget(db.Model):
    """Modelo de Orçamento por Categoria para um determinado mês."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    budget_amount = db.Column(db.Float, nullable=False)
    month_year = db.Column(db.String(7), nullable=False) # 'YYYY-MM'
    current_spent = db.Column(db.Float, default=0.0, nullable=False) # Valor já gasto nesta categoria no mês

    # Garante que um usuário não tenha dois orçamentos para a mesma categoria no mesmo mês
    __table_args__ = (db.UniqueConstraint('user_id', 'category_id', 'month_year', name='_user_category_month_uc'),)

    def __repr__(self):
        return f"<Budget {self.category.name} for {self.month_year}: {self.budget_amount}>"

class Goal(db.Model):
    """Modelo de Meta Financeira."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    target_amount = db.Column(db.Float, nullable=False) # Valor total a ser alcançado
    current_amount = db.Column(db.Float, default=0.0, nullable=False) # Valor atual contribuído
    due_date = db.Column(db.String(10), nullable=True) # Data limite YYYY-MM-DD
    status = db.Column(db.String(20), default='in_progress', nullable=False) # 'in_progress', 'achieved', 'abandoned'
    transactions = db.relationship('Transaction', backref='goal', lazy=True) # Transações associadas a esta meta

    def __repr__(self):
        return f"<Goal {self.name}: {self.current_amount}/{self.target_amount}>"

