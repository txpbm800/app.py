import datetime
import calendar
import json
import random
import string
import smtplib
import ssl
from email.mime.text import MIMEText

# Importa os modelos e o objeto db do models.py
from models import db, User, Category, Account, Transaction, Bill, Budget, Goal
from config import Config # Importa as configurações

# Para cálculo de datas recorrentes
from dateutil.relativedelta import relativedelta 

# Para exportação de relatórios
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.styles.numbers import FORMAT_CURRENCY_USD_SIMPLE
from fpdf import FPDF
import io

# Importa flash e jsonify de Flask para uso em funções de serviço que podem precisar delas
# (embora idealmente as funções de serviço retornem dados e as rotas lidem com flash/jsonify)
from flask import flash, jsonify

# Configuração do Gemini (será configurada no app.py, mas as funções que o usam estão aqui)
import google.generativeai as genai

# Data de hoje para consistência
TODAY_DATE = datetime.date.today()

# --- Funções Auxiliares Gerais ---

def get_current_month_year_str():
    """Retorna o mês e ano atuais no formato YYYY-MM."""
    return TODAY_DATE.strftime('%Y-%m')

def get_month_start_end_dates(month_year_str):
    """
    Retorna as datas de início e fim de um mês/ano específico.
    Args:
        month_year_str (str): Mês e ano no formato 'YYYY-MM'.
    Returns:
        tuple: (start_date, end_date) como objetos datetime.date.
    """
    year, month = map(int, month_year_str.split('-'))
    start_date = datetime.date(year, month, 1)
    end_date = start_date.replace(day=calendar.monthrange(year, month)[1])
    return start_date, end_date

# --- Funções de Transação ---

def add_transaction_db(description, amount, date, type, user_id, category_id=None, account_id=None, goal_id=None):
    """
    Adiciona uma nova transação ao banco de dados e atualiza saldos/orçamentos/metas.
    """
    amount = float(amount)
    date_obj = datetime.datetime.strptime(date, '%Y-%m-%d').date()

    new_transaction = Transaction(
        description=description,
        amount=amount,
        date=date,
        type=type,
        user_id=user_id,
        category_id=category_id,
        account_id=account_id,
        goal_id=goal_id
    )
    db.session.add(new_transaction)
    db.session.flush() # Flush para obter o ID da transação antes do commit

    # Atualiza o saldo da conta
    if account_id:
        account = Account.query.get(account_id)
        if account:
            if type == 'income':
                account.balance += amount
            else: # expense
                account.balance -= amount
            db.session.add(account)

    # Atualiza o orçamento (se for despesa e tiver categoria)
    if type == 'expense' and category_id:
        transaction_month_year = date_obj.strftime('%Y-%m')
        budget = Budget.query.filter_by(
            user_id=user_id,
            category_id=category_id,
            month_year=transaction_month_year
        ).first()
        if budget:
            budget.current_spent += amount
            db.session.add(budget)

    # Atualiza a meta (se houver)
    if goal_id:
        goal = Goal.query.get(goal_id)
        if goal and goal.user_id == user_id:
            goal.current_amount += amount
            if goal.current_amount >= goal.target_amount:
                goal.status = 'achieved'
                flash(f'Parabéns! Com esta transação, a meta "{goal.name}" foi atingida!', 'success')
            db.session.add(goal)
    
    db.session.commit()
    return new_transaction

def edit_transaction_db(transaction_id, description, amount, date, type, user_id, category_id=None, account_id=None, goal_id=None):
    """
    Edita uma transação existente e reverte/aplica as mudanças nos saldos/orçamentos/metas.
    """
    transaction = Transaction.query.filter_by(id=transaction_id, user_id=user_id).first()
    if not transaction:
        return False

    old_amount = transaction.amount
    old_type = transaction.type
    old_category_id = transaction.category_id
    old_account_id = transaction.account_id
    old_goal_id = transaction.goal_id
    old_date_obj = datetime.datetime.strptime(transaction.date, '%Y-%m-%d').date()

    new_amount = float(amount)
    new_date_obj = datetime.datetime.strptime(date, '%Y-%m-%d').date()

    # 1. Reverte o impacto da transação antiga
    if old_account_id:
        old_account = Account.query.get(old_account_id)
        if old_account:
            if old_type == 'income': old_account.balance -= old_amount
            else: old_account.balance += old_amount
            db.session.add(old_account)
    
    if old_type == 'expense' and old_category_id:
        old_budget = Budget.query.filter_by(user_id=user_id, category_id=old_category_id, month_year=old_date_obj.strftime('%Y-%m')).first()
        if old_budget: 
            old_budget.current_spent -= old_amount
            db.session.add(old_budget)
    
    if old_goal_id:
        old_goal = Goal.query.get(old_goal_id)
        if old_goal and old_goal.user_id == user_id:
            old_goal.current_amount -= old_amount
            if old_goal.status == 'achieved' and old_goal.current_amount < old_goal.target_amount:
                old_goal.status = 'in_progress'
            db.session.add(old_goal)

    # 2. Atualiza a transação com os novos dados
    transaction.description = description
    transaction.amount = new_amount
    transaction.date = date
    transaction.type = type
    transaction.category_id = category_id
    transaction.account_id = account_id
    transaction.goal_id = goal_id

    # 3. Aplica o impacto da nova transação
    if account_id:
        new_account = Account.query.get(account_id)
        if new_account:
            if type == 'income': new_account.balance += new_amount
            else: new_account.balance -= new_amount
            db.session.add(new_account)
    
    if type == 'expense' and category_id:
        new_budget_month_year = new_date_obj.strftime('%Y-%m')
        new_budget = Budget.query.filter_by(user_id=user_id, category_id=category_id, month_year=new_budget_month_year).first()
        if new_budget: 
            new_budget.current_spent += new_amount
            db.session.add(new_budget)
        else:
            # Se a categoria/mês mudou e não há orçamento, cria um novo (opcional, dependendo da regra de negócio)
            # Ou simplesmente não atualiza o orçamento se não existir
            pass
    
    if goal_id:
        new_goal = Goal.query.get(goal_id)
        if new_goal and new_goal.user_id == user_id:
            new_goal.current_amount += new_amount
            if new_goal.current_amount >= new_goal.target_amount:
                new_goal.status = 'achieved'
                flash(f'Parabéns! Com esta transação, a meta "{new_goal.name}" foi atingida!', 'success')
            db.session.add(new_goal)

    db.session.commit()
    return True

def delete_transaction_db(transaction_id, user_id):
    """
    Exclui uma transação e reverte seu impacto em saldos/orçamentos/metas.
    """
    transaction = Transaction.query.filter_by(id=transaction_id, user_id=user_id).first()
    if not transaction:
        return False

    # Reverte o saldo da conta
    if transaction.account_id:
        account = Account.query.get(transaction.account_id)
        if account:
            if transaction.type == 'income':
                account.balance -= transaction.amount
            else: # expense
                account.balance += transaction.amount
            db.session.add(account)

    # Reverte o orçamento (se for despesa e tiver categoria)
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

    # Reverte a meta (se houver)
    if transaction.goal_id:
        goal = Goal.query.get(transaction.goal_id)
        if goal and goal.user_id == user_id:
            goal.current_amount -= transaction.amount
            if goal.status == 'achieved' and goal.current_amount < goal.target_amount:
                goal.status = 'in_progress'
            db.session.add(goal)
    
    db.session.delete(transaction)
    db.session.commit()
    return True

# --- Funções de Contas a Pagar/Receber (Bills) ---

def _generate_future_recurring_bills(master_bill):
    """
    Gera as ocorrências futuras de uma conta mestra recorrente.
    Limpa as ocorrências pendentes existentes para evitar duplicatas.
    """
    print(f"DEBUG: _generate_future_recurring_bills chamada para master_bill ID: {master_bill.id}, Desc: {master_bill.description}")
    
    if not master_bill.id:
        print("ERROR: Master Bill não tem um ID ainda. Não é possível gerar filhos.")
        return

    # Deleta todas as ocorrências filhas PENDENTES para esta mestra para evitar duplicatas
    # Isso é importante para garantir que a regeneração seja limpa
    Bill.query.filter_by(recurring_parent_id=master_bill.id, user_id=master_bill.user_id, status='pending').delete()
    db.session.commit() # Commit da exclusão antes de adicionar novos

    generated_count_for_master = 0
    
    # Define o total a ser gerado. Se 0, gera um número padrão (ex: 12 meses)
    total_to_generate = master_bill.recurring_total_occurrences if master_bill.recurring_total_occurrences and master_bill.recurring_total_occurrences > 0 else 12
    
    print(f"DEBUG: Total de ocorrências para gerar para {master_bill.description}: {total_to_generate}")

    for i in range(1, total_to_generate + 1):
        occurrence_date_for_child = datetime.datetime.strptime(master_bill.recurring_start_date, '%Y-%m-%d').date()
        
        # Calcula a data da ocorrência com base na frequência
        if master_bill.recurring_frequency == 'monthly' or master_bill.recurring_frequency == 'installments':
            occurrence_date_for_child += relativedelta(months=i-1)
        elif master_bill.recurring_frequency == 'weekly':
            occurrence_date_for_child += relativedelta(weeks=i-1)
        elif master_bill.recurring_frequency == 'yearly':
            occurrence_date_for_child += relativedelta(years=i-1)

        # Verifica se já existe uma ocorrência filha PAGA para esta data e número
        # Não queremos recriar contas que já foram pagas
        existing_paid_child = Bill.query.filter_by(
            recurring_parent_id=master_bill.id,
            dueDate=occurrence_date_for_child.isoformat(),
            recurring_child_number=i,
            user_id=master_bill.user_id,
            status='paid'
        ).first()

        if existing_paid_child:
            print(f"    Bill filha já existe e está PAGA para {master_bill.description}, ocorrência {i} em {occurrence_date_for_child.isoformat()}, pulando.")
            # Se já está paga, não precisa gerar novamente, mas conta como "gerada" para o total
            generated_count_for_master += 1
            continue # Pula para a próxima iteração

        # Se não existe ou se existe mas não está paga (e foi deletada no início da função), cria uma nova
        if master_bill.recurring_frequency == 'installments' and master_bill.recurring_total_occurrences > 0:
            child_description = f"{master_bill.description.replace(' (Mestra)', '')} (Parcela {i}/{master_bill.recurring_total_occurrences})"
        else:
            child_description = master_bill.description.replace(' (Mestra)', '')

        new_child_bill_status = 'pending'
        if occurrence_date_for_child < TODAY_DATE:
            new_child_bill_status = 'overdue'

        new_child_bill = Bill(
            description=child_description,
            amount=master_bill.amount,
            dueDate=occurrence_date_for_child.isoformat(),
            status=new_child_bill_status,
            user_id=master_bill.user_id,
            recurring_parent_id=master_bill.id,
            recurring_child_number=i,
            is_master_recurring_bill=False, # Filhos não são mestres
            recurring_frequency=None,
            recurring_start_date=None,
            recurring_next_due_date=None,
            recurring_total_occurrences=0,
            recurring_installments_generated=0,
            is_active_recurring=False,
            type=master_bill.type,
            category_id=master_bill.category_id,
            account_id=master_bill.account_id
        )
        db.session.add(new_child_bill)
        generated_count_for_master += 1
        print(f"    Gerada Bill filha: {child_description} em {occurrence_date_for_child.isoformat()} com status {new_child_bill_status}")

    master_bill.recurring_installments_generated = generated_count_for_master
    
    # Atualiza a próxima data de vencimento da conta mestra para a próxima ocorrência
    if master_bill.recurring_total_occurrences > 0:
        next_occurrence_number_for_master = master_bill.recurring_installments_generated + 1
        if next_occurrence_number_for_master > master_bill.recurring_total_occurrences:
            master_bill.is_active_recurring = False
            master_bill.recurring_next_due_date = None # Não há mais datas futuras
        else:
            next_due_date_for_master = datetime.datetime.strptime(master_bill.recurring_start_date, '%Y-%m-%d').date()
            if master_bill.recurring_frequency == 'monthly' or master_bill.recurring_frequency == 'installments':
                next_due_date_for_master += relativedelta(months=next_occurrence_number_for_master - 1)
            elif master_bill.recurring_frequency == 'weekly':
                next_due_date_for_master += relativedelta(weeks=next_occurrence_number_for_master - 1)
            elif master_bill.recurring_frequency == 'yearly':
                next_due_date_for_master += relativedelta(years=next_occurrence_number_for_master - 1)
            master_bill.recurring_next_due_date = next_due_date_for_master.isoformat()
            print(f"DEBUG: Próximo vencimento da semente '{master_bill.description}' atualizado para: {master_bill.recurring_next_due_date}")
    else: # Frequência indefinida (recurring_total_occurrences é 0)
        # O próximo vencimento é simplesmente o próximo período a partir de HOJE
        next_due_date_for_master = TODAY_DATE
        if master_bill.recurring_frequency == 'monthly':
            next_due_date_for_master += relativedelta(months=1)
        elif master_bill.recurring_frequency == 'weekly':
            next_due_date_for_master += relativedelta(weeks=1)
        elif master_bill.recurring_frequency == 'yearly':
            next_due_date_for_master += relativedelta(years=1)
        
        master_bill.recurring_next_due_date = next_due_date_for_master.isoformat()
        print(f"DEBUG: Próximo vencimento da semente '{master_bill.description}' (indefinida) atualizado para: {master_bill.recurring_next_due_date}")

    db.session.add(master_bill)
    db.session.commit()


def process_recurring_bills_on_access(user_id):
    """
    Processa contas mestras recorrentes que precisam gerar novas ocorrências
    sempre que o usuário acessa o dashboard.
    """
    recurring_seed_bills_to_process = Bill.query.filter(
        Bill.user_id == user_id,
        Bill.is_master_recurring_bill == True,
        Bill.is_active_recurring == True,
        # Converte para data para comparação correta
        db.cast(Bill.recurring_next_due_date, db.Date) <= TODAY_DATE
    ).all()
    
    print(f"\n--- process_recurring_bills_on_access chamada. Processando {len(recurring_seed_bills_to_process)} contas mestras recorrentes devidas ---")

    for bill_seed in recurring_seed_bills_to_process:
        print(f"    Acionando geração em massa para mestra '{bill_seed.description}' (ID: {bill_seed.id}) por estar vencida.")
        _generate_future_recurring_bills(bill_seed)
        
def add_bill_db(description, amount, due_date, user_id, 
                is_recurring=False, recurring_frequency=None, recurring_total_occurrences=0, bill_type='expense', category_id=None, account_id=None):
    """
    Adiciona uma nova conta a pagar/receber, com suporte a recorrência.
    """
    amount = float(amount)
    
    # Ajusta total de ocorrências para parcelas ou 0 para indefinido
    if is_recurring and recurring_frequency == 'installments' and (recurring_total_occurrences is None or recurring_total_occurrences < 1):
        recurring_total_occurrences = 1 # Garante pelo menos 1 parcela
    elif not is_recurring or recurring_frequency != 'installments':
        recurring_total_occurrences = 0 # Não recorrente ou recorrente indefinido

    new_bill = Bill(
        description=description,
        amount=amount,
        dueDate=due_date,
        status='pending',
        user_id=user_id,
        is_master_recurring_bill=is_recurring,
        recurring_frequency=recurring_frequency if is_recurring else None,
        recurring_start_date=due_date if is_recurring else None,
        recurring_next_due_date=due_date if is_recurring else None,
        recurring_total_occurrences=recurring_total_occurrences,
        recurring_installments_generated=0,
        is_active_recurring=is_recurring,
        type=bill_type,
        category_id=category_id,
        account_id=account_id
    )
    db.session.add(new_bill)
    db.session.commit() # Commit para que new_bill tenha um ID

    # Se for uma conta mestra recorrente, gera as ocorrências futuras imediatamente
    if new_bill.is_master_recurring_bill and new_bill.is_active_recurring:
        _generate_future_recurring_bills(new_bill)

def pay_bill_db(bill_id, user_id, payment_account_id):
    """
    Marca uma conta como paga e cria uma transação de despesa associada.
    Verifica saldo da conta.
    """
    bill = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if not bill:
        flash('Conta não encontrada ou não pertence a você.', 'danger')
        return False

    if bill.status == 'paid':
        flash(f'A conta "{bill.description}" já está paga.', 'warning')
        return False

    account = Account.query.get(payment_account_id)
    if not account or account.user_id != user_id:
        flash('Conta de pagamento inválida ou não pertence a você.', 'danger')
        return False

    if account.balance < bill.amount:
        flash(f'Saldo insuficiente na conta "{account.name}" para pagar a conta "{bill.description}".', 'danger')
        return False

    # Tenta encontrar uma categoria para o pagamento
    category_for_payment_id = bill.category_id
    if not category_for_payment_id:
        # Tenta usar categorias padrão se nenhuma for definida na conta
        default_cat = Category.query.filter_by(user_id=user_id, name='Contas Fixas', type='expense').first()
        if not default_cat:
            default_cat = Category.query.filter_by(user_id=user_id, name='Outras Despesas', type='expense').first()
        if default_cat:
            category_for_payment_id = default_cat.id
        else:
            flash("Aviso: Categoria padrão para pagamento de conta não encontrada. A transação pode não ser categorizada corretamente.", 'warning')
            category_for_payment_id = None
            
    # Cria a transação de pagamento
    new_payment_transaction = add_transaction_db(
        description=f"Pagamento: {bill.description}",
        amount=bill.amount,
        date=TODAY_DATE.isoformat(),
        type='expense', # Pagamento de conta é sempre despesa
        user_id=user_id,
        category_id=category_for_payment_id,
        account_id=payment_account_id
    )

    if new_payment_transaction:
        bill.payment_transaction_id = new_payment_transaction.id
        bill.status = 'paid'
        db.session.add(bill)
        db.session.commit()
        
        # Se for uma conta filha de uma recorrência, tenta regenerar a série
        master_bill_to_process = None
        if bill.is_master_recurring_bill:
            master_bill_to_process = bill
        elif bill.recurring_parent_id:
            master_bill_to_process = Bill.query.filter_by(id=bill.recurring_parent_id, user_id=user_id, is_master_recurring_bill=True).first()

        if master_bill_to_process and master_bill_to_process.is_active_recurring:
            _generate_future_recurring_bills(master_bill_to_process)

        return True
    
    return False


def reschedule_bill_db(bill_id, new_date, user_id):
    """Remarca a data de vencimento de uma conta."""
    bill = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if bill:
        bill.dueDate = new_date
        # Se a nova data for futura e o status era 'overdue', muda para 'pending'
        if datetime.datetime.strptime(new_date, '%Y-%m-%d').date() >= TODAY_DATE and bill.status == 'overdue':
            bill.status = 'pending'
        db.session.add(bill)
        db.session.commit()
        return True
    return False

def delete_bill_db(bill_id, user_id):
    """
    Exclui uma conta. Se for uma conta mestra recorrente, exclui todas as filhas.
    Se for uma conta filha, cancela a série mestra e exclui todas as filhas (incluindo a mestra).
    Reverte a transação de pagamento se a conta estava paga.
    """
    bill = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if not bill:
        return False

    print(f"DEBUG: Deletando Conta ID: {bill.id}, Desc: '{bill.description}', É Mestra: {bill.is_master_recurring_bill}, ID Pai: {bill.recurring_parent_id}")

    # Reverte a transação de pagamento se a conta estava paga
    if bill.status == 'paid' and bill.payment_transaction_id:
        payment_transaction = Transaction.query.get(bill.payment_transaction_id)
        if payment_transaction and payment_transaction.user_id == user_id:
            # Reverte o saldo da conta de pagamento
            if payment_transaction.account_id:
                account = Account.query.get(payment_transaction.account_id)
                if account:
                    account.balance += payment_transaction.amount
                    db.session.add(account)

            # Reverte o orçamento
            if payment_transaction.type == 'expense' and payment_transaction.category_id:
                transaction_month_year = datetime.datetime.strptime(payment_transaction.date, '%Y-%m-%d').strftime('%Y-%m')
                budget = Budget.query.filter_by(
                    user_id=user_id,
                    category_id=payment_transaction.category_id,
                    month_year=transaction_month_year
                ).first()
                if budget:
                    budget.current_spent -= payment_transaction.amount
                    db.session.add(budget)
                    print(f"DEBUG: Orçamento {budget.category.name} revertido para pagamento excluído. Gasto atual: {budget.current_spent}")
            db.session.delete(payment_transaction)
            print(f"DEBUG: Transação de pagamento associada ID: {payment_transaction.id} excluída.")
        else:
            print(f"AVISO: Transação de pagamento associada ID {bill.payment_transaction_id} não encontrada ou não pertence ao usuário, não foi possível reverter.")

    if bill.is_master_recurring_bill:
        # Se for uma conta mestra, exclui todas as suas filhas (pendentes e pagas)
        child_bills = Bill.query.filter_by(recurring_parent_id=bill.id, user_id=user_id).all()
        for child_bill in child_bills:
            # Se a filha estava paga, desvincula a transação de pagamento para não deletá-la duas vezes
            if child_bill.status == 'paid' and child_bill.payment_transaction_id:
                child_bill.payment_transaction_id = None # Desvincula
                db.session.add(child_bill)
            db.session.delete(child_bill)
            print(f"DEBUG: Deletando conta filha: ID {child_bill.id}, Desc: '{child_bill.description}'")
        print(f"DEBUG: {len(child_bills)} contas filhas excluídas para a mestra '{bill.description}'.")
        
        db.session.delete(bill) # Finalmente, exclui a conta mestra
        db.session.commit()
        print(f"DEBUG: Conta mestra '{bill.description}' (ID: {bill.id}) e suas filhas excluídas.")
        return True

    elif bill.recurring_parent_id:
        # Se for uma conta filha, cancela a série mestra e exclui todas as filhas (incluindo a mestra)
        master_bill = Bill.query.filter_by(id=bill.recurring_parent_id, user_id=user_id, is_master_recurring_bill=True).first()
        if master_bill:
            print(f"DEBUG: Conta filha '{bill.description}' (ID: {bill.id}) sendo excluída. Tentando cancelar a série mestra '{master_bill.description}'.")
            
            # Desativa a conta mestra
            master_bill.is_active_recurring = False
            master_bill.recurring_frequency = None
            master_bill.recurring_start_date = None
            master_bill.recurring_next_due_date = None
            master_bill.recurring_total_occurrences = 0
            master_bill.recurring_installments_generated = 0
            db.session.add(master_bill)

            # Exclui todas as outras contas filhas (pendentes) da série
            all_children_of_master = Bill.query.filter_by(recurring_parent_id=master_bill.id, user_id=user_id).all()
            for child in all_children_of_master:
                # Se a filha estava paga, desvincula a transação de pagamento
                if child.status == 'paid' and child.payment_transaction_id:
                    child.payment_transaction_id = None
                    db.session.add(child)
                db.session.delete(child)
                print(f"DEBUG: Deletando outra conta filha (da mestra): ID {child.id}, Desc: '{child.description}'")
            
            db.session.delete(master_bill) # Exclui a própria conta mestra
            db.session.commit()
            print(f"DEBUG: Série recorrente para a mestra '{master_bill.description}' (ID: {master_bill.id}) cancelada e todas as suas filhas excluídas.")
            return True
        else:
            # Se a conta filha não tem uma mestra válida (erro de dados ou mestra já excluída)
            print(f"DEBUG: Conta filha '{bill.description}' (ID: {bill.id}) excluída, mas conta mestra recorrente (ID: {bill.recurring_parent_id}) não encontrada ou não é uma mestra.")
            db.session.delete(bill) # Apenas exclui a conta filha
            db.session.commit()
            return True
    else:
        # Se não é recorrente nem filha de recorrente, apenas exclui a conta
        db.session.delete(bill)
        db.session.commit()
        print(f"DEBUG: Conta não recorrente '{bill.description}' (ID: {bill.id}) excluída.")
        return True


def edit_bill_db(bill_id, description, amount, dueDate, user_id, 
                   is_recurring=False, recurring_frequency=None, recurring_total_occurrences=0, is_active_recurring=False, bill_type='expense', category_id=None, account_id=None):
    """
    Edita uma conta existente, com lógica para recorrência.
    """
    bill = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if not bill:
        return False

    bill.description = description
    bill.amount = float(amount)
    bill.dueDate = dueDate
    bill.type = bill_type
    bill.category_id = category_id
    bill.account_id = account_id

    if bill.status == 'paid':
        print(f"AVISO: Conta {bill.id} já está paga. Alterações em valor/tipo/categoria não atualizarão a transação/orçamento passados.")

    # Lógica para gerenciar a recorrência da conta
    if is_recurring:
        # Se a conta se torna recorrente ou seus parâmetros de recorrência mudam
        old_is_master = bill.is_master_recurring_bill
        old_is_active = bill.is_active_recurring
        old_frequency = bill.recurring_frequency
        old_total_occurrences = bill.recurring_total_occurrences

        bill.is_master_recurring_bill = True # Agora é uma mestra
        bill.is_active_recurring = is_active_recurring # Controla se está ativa
        bill.recurring_frequency = recurring_frequency
        bill.recurring_start_date = bill.dueDate # A data de vencimento atual se torna a data de início da série
        bill.recurring_total_occurrences = recurring_total_occurrences if recurring_frequency == 'installments' else 0
        bill.recurring_parent_id = None # Uma mestra não tem pai

        regenerate = False
        if not old_is_master: # Se era uma conta normal e virou recorrente
            regenerate = True
        elif is_active_recurring and (not old_is_active or old_frequency != recurring_frequency or old_total_occurrences != recurring_total_occurrences):
            # Se já era mestra e ativa, e a frequência ou total de ocorrências mudou
            regenerate = True
        
        if regenerate:
            print(f"DEBUG: Editando conta mestra '{bill.description}'. Parâmetros de recorrência alterados ou reativados. Regenerando ocorrências futuras.")
            _generate_future_recurring_bills(bill)
        elif old_is_master and not is_active_recurring:
            # Se era mestra e foi desativada manualmente
            bill.is_active_recurring = False
            bill.recurring_frequency = None
            bill.recurring_next_due_date = None
            bill.recurring_total_occurrences = 0
            bill.recurring_installments_generated = 0
            # Deleta as ocorrências futuras pendentes, mas mantém as pagas
            Bill.query.filter_by(recurring_parent_id=bill.id, user_id=user_id, status='pending').delete()
            print(f"DEBUG: Conta mestra '{bill.description}' desativada, contas filhas futuras pendentes excluídas.")

    else: # Se a conta não é mais recorrente
        if bill.is_master_recurring_bill:
            # Se era uma mestra e foi convertida para não recorrente
            Bill.query.filter_by(recurring_parent_id=bill.id, user_id=user_id, status='pending').delete()
            print(f"DEBUG: Conta mestra '{bill.description}' convertida para não recorrente. Filhas pendentes excluídas.")
        
        bill.is_master_recurring_bill = False
        bill.is_active_recurring = False
        bill.recurring_frequency = None
        bill.recurring_start_date = None
        bill.recurring_next_due_date = None
        bill.recurring_total_occurrences = 0
        bill.recurring_installments_generated = 0
        bill.recurring_parent_id = None # Garante que não é mais filha de ninguém

    db.session.commit()
    return True

def get_dashboard_data_db(user_id):
    """
    Busca os dados do dashboard para o usuário logado.
    """
    current_month_start = TODAY_DATE.replace(day=1)
    next_month_start = (current_month_start + relativedelta(months=1))
    
    start_date_str = current_month_start.isoformat()
    end_date_str = next_month_start.isoformat() # Usar < para esta data

    # Saldo total de todas as contas
    total_balance = db.session.query(db.func.sum(Account.balance)).filter_by(user_id=user_id).scalar() or 0.0

    # Receitas do mês atual
    monthly_income = db.session.query(db.func.sum(Transaction.amount)).filter(
        Transaction.user_id == user_id,
        Transaction.type == 'income',
        Transaction.date >= start_date_str,
        Transaction.date < end_date_str
    ).scalar() or 0.0

    # Despesas do mês atual
    monthly_expenses = db.session.query(db.func.sum(Transaction.amount)).filter(
        Transaction.user_id == user_id,
        Transaction.type == 'expense',
        Transaction.date >= start_date_str,
        Transaction.date < end_date_str
    ).scalar() or 0.0
    
    # Valor total das contas pendentes (mês atual e atrasadas)
    monthly_pending_bills_amount = db.session.query(db.func.sum(Bill.amount)).filter(
        Bill.user_id == user_id,
        Bill.status == 'pending',
        Bill.is_master_recurring_bill == False, # Apenas contas "filhas" ou não recorrentes
        db.cast(Bill.dueDate, db.Date) < next_month_start # Inclui atrasadas e do mês atual
    ).scalar() or 0.0

    # Lista de todas as contas pendentes (mês atual e atrasadas)
    all_pending_bills_list = Bill.query.filter(
        Bill.user_id == user_id,
        Bill.status == 'pending',
        Bill.is_master_recurring_bill == False,
        db.cast(Bill.dueDate, db.Date) < next_month_start
    ).order_by(db.cast(Bill.dueDate, db.Date).asc()).all()

    return {
        'balance': total_balance,
        'totalIncome': monthly_income,
        'totalExpenses': monthly_expenses,
        'totalPendingBills': monthly_pending_bills_amount,
        'pendingBillsList': all_pending_bills_list
    }

# --- Funções de Orçamento ---

def add_budget_db(user_id, category_id, budget_amount, month_year):
    """
    Adiciona um novo orçamento ou atualiza um existente para uma categoria e mês.
    Recalcula o 'current_spent' para garantir que esteja atualizado.
    """
    existing_budget = Budget.query.filter_by(
        user_id=user_id,
        category_id=category_id,
        month_year=month_year
    ).first()

    if existing_budget:
        existing_budget.budget_amount = float(budget_amount)
        db.session.add(existing_budget) # Marca para atualização
    else:
        new_budget = Budget(
            user_id=user_id,
            category_id=category_id,
            budget_amount=float(budget_amount),
            month_year=month_year
        )
        db.session.add(new_budget)
    
    # Recalcula o current_spent para o orçamento recém-adicionado/atualizado
    start_date, end_date = get_month_start_end_dates(month_year)
    start_date_str = start_date.isoformat()
    end_date_str = end_date.isoformat()

    total_spent_in_category = db.session.query(db.func.sum(Transaction.amount)).filter(
        Transaction.user_id == user_id,
        Transaction.category_id == category_id,
        Transaction.type == 'expense',
        Transaction.date >= start_date_str,
        Transaction.date <= end_date_str
    ).scalar() or 0.0

    if existing_budget:
        existing_budget.current_spent = total_spent_in_category
    else: # new_budget
        new_budget.current_spent = total_spent_in_category

    db.session.commit()
    return True

def edit_budget_db(budget_id, user_id, budget_amount=None):
    """
    Edita o valor de um orçamento existente.
    Recalcula o 'current_spent' para garantir que esteja atualizado.
    """
    budget = Budget.query.filter_by(id=budget_id, user_id=user_id).first()
    if budget:
        if budget_amount is not None:
            budget.budget_amount = float(budget_amount)
        
        # Recalcula o current_spent para o orçamento atualizado
        start_date, end_date = get_month_start_end_dates(budget.month_year)
        start_date_str = start_date.isoformat()
        end_date_str = end_date.isoformat()
        
        total_spent_in_category = db.session.query(db.func.sum(Transaction.amount)).filter(
            Transaction.user_id == user_id,
            Transaction.category_id == budget.category_id,
            Transaction.type == 'expense',
            Transaction.date >= start_date_str,
            Transaction.date <= end_date_str
        ).scalar() or 0.0
        budget.current_spent = total_spent_in_category
        
        db.session.commit()
        return True
    return False

def delete_budget_db(budget_id, user_id):
    """Exclui um orçamento."""
    budget = Budget.query.filter_by(id=budget_id, user_id=user_id).first()
    if budget:
        db.session.delete(budget)
        db.session.commit()
        return True
    return False

# --- Funções de Meta ---

def add_goal_db(user_id, name, target_amount, due_date=None):
    """Adiciona uma nova meta financeira."""
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
    """Edita uma meta financeira existente."""
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
    """Exclui uma meta financeira."""
    goal = Goal.query.filter_by(id=goal_id, user_id=user_id).first()
    if goal:
        db.session.delete(goal)
        db.session.commit()
        return True
    return False

def contribute_to_goal_db(goal_id, user_id, amount, source_account_id):
    """
    Registra uma contribuição para uma meta, criando uma transação de despesa
    e debitando da conta de origem.
    """
    goal = Goal.query.filter_by(id=goal_id, user_id=user_id).first()
    if not goal:
        flash('Meta não encontrada ou não pertence a você.', 'danger')
        return False
    
    amount_to_add = float(amount)
    if amount_to_add <= 0:
        flash('O valor da contribuição deve ser maior que zero.', 'danger')
        return False

    source_account = Account.query.get(source_account_id)
    if not source_account or source_account.user_id != user_id:
        flash('Conta de origem inválida ou não pertence a você.', 'danger')
        return False

    if source_account.balance < amount_to_add:
        flash(f'Saldo insuficiente na conta "{source_account.name}" para contribuir com a meta "{goal.name}".', 'danger')
        return False

    # Calcula o valor real a ser adicionado para não ultrapassar o alvo
    amount_to_add_actual = min(amount_to_add, goal.target_amount - goal.current_amount)

    if amount_to_add_actual <= 0:
        flash(f'A meta "{goal.name}" já foi atingida ou o valor é insuficiente.', 'info')
        return False

    goal.current_amount += amount_to_add_actual
    if goal.current_amount >= goal.target_amount:
        goal.status = 'achieved'
        flash(f'Parabéns! Com esta contribuição, a meta "{goal.name}" foi atingida!', 'success')
    else:
        flash(f'Contribuição de R$ {amount_to_add_actual:.2f} adicionada à meta "{goal.name}".', 'success')
        
    db.session.add(goal)

    # Cria uma transação de despesa na categoria "Poupança para Metas"
    poupanca_metas_category = Category.query.filter_by(name='Poupança para Metas', type='expense', user_id=user_id).first()
    
    if poupanca_metas_category:
        new_transaction = add_transaction_db(
            description=f"Contribuição para Meta: {goal.name}",
            amount=amount_to_add_actual,
            date=TODAY_DATE.isoformat(),
            type='expense',
            user_id=user_id,
            category_id=poupanca_metas_category.id,
            account_id=source_account_id,
            goal_id=goal.id # Associa a transação à meta
        )
        if not new_transaction:
            print("WARNING: Falha ao criar transação para contribuição de meta.")
            flash("Aviso: Falha ao registrar a transação para esta contribuição de meta.", 'warning')
    else:
        print("WARNING: Não foi possível criar transação para contribuição de meta (categoria 'Poupança para Metas' ausente).")
        flash("Aviso: Categoria 'Poupança para Metas' não encontrada. A transação da meta não foi registrada.", 'warning')

    db.session.commit()
    return True

# --- Funções de Gerenciamento de Contas ---

def add_account_db(user_id, name, initial_balance):
    """Adiciona uma nova conta para o usuário."""
    existing_account = Account.query.filter_by(user_id=user_id, name=name).first()
    if existing_account:
        flash('Uma conta com este nome já existe.', 'danger')
        return False
    
    new_account = Account(
        user_id=user_id,
        name=name,
        balance=float(initial_balance)
    )
    db.session.add(new_account)
    db.session.commit()
    return True

def edit_account_db(account_id, user_id, new_name=None, new_balance=None):
    """Edita uma conta existente do usuário."""
    account = Account.query.filter_by(id=account_id, user_id=user_id).first()
    if not account:
        flash('Conta não encontrada ou não pertence a você.', 'danger')
        return False
    
    if new_name and new_name != account.name:
        existing_account_with_new_name = Account.query.filter_by(user_id=user_id, name=new_name).first()
        if existing_account_with_new_name and existing_account_with_new_name.id != account_id:
            flash('Já existe outra conta com este nome.', 'danger')
            return False
        account.name = new_name
    
    if new_balance is not None:
        account.balance = float(new_balance)
    
    db.session.commit()
    return True

def delete_account_db(account_id, user_id):
    """Exclui uma conta e desvincula suas transações."""
    account = Account.query.filter_by(id=account_id, user_id=user_id).first()
    if not account:
        flash('Conta não encontrada ou não pertence a você.', 'danger')
        return False
    
    # Desvincula todas as transações e contas a pagar associadas
    Transaction.query.filter_by(account_id=account_id, user_id=user_id).update({'account_id': None})
    Bill.query.filter_by(account_id=account_id, user_id=user_id).update({'account_id': None})
    
    db.session.delete(account)
    db.session.commit()
    return True

def transfer_funds_db(user_id, source_account_id, destination_account_id, amount):
    """Transfere fundos entre duas contas do usuário."""
    amount = float(amount)
    if amount <= 0:
        flash('O valor da transferência deve ser maior que zero.', 'danger')
        return False

    if source_account_id == destination_account_id:
        flash('A conta de origem e a conta de destino não podem ser a mesma.', 'danger')
        return False

    source_account = Account.query.filter_by(id=source_account_id, user_id=user_id).first()
    destination_account = Account.query.filter_by(id=destination_account_id, user_id=user_id).first()

    if not source_account or not destination_account:
        flash('Conta de origem ou destino não encontrada.', 'danger')
        return False

    if source_account.balance < amount:
        flash(f'Saldo insuficiente na conta de origem ({source_account.name}) para realizar a transferência.', 'danger')
        return False

    # Debitar da conta de origem
    source_account.balance -= amount
    db.session.add(source_account)

    # Creditar na conta de destino
    destination_account.balance += amount
    db.session.add(destination_account)

    # Criar transação de despesa para a conta de origem (sem categoria)
    add_transaction_db(
        description=f"Transferência para {destination_account.name}",
        amount=amount,
        date=TODAY_DATE.isoformat(),
        type='expense', # Para fins de registro, é uma saída da conta de origem
        user_id=user_id,
        category_id=None, # Transferências não afetam categorias de despesa/receita primárias
        account_id=source_account_id
    )

    # Criar transação de receita para a conta de destino (sem categoria)
    add_transaction_db(
        description=f"Transferência de {source_account.name}",
        amount=amount,
        date=TODAY_DATE.isoformat(),
        type='income', # Para fins de registro, é uma entrada na conta de destino
        user_id=user_id,
        category_id=None, # Transferências não afetam categorias de despesa/receita primárias
        account_id=destination_account_id
    )

    db.session.commit()
    return True

# --- Funções para Relatórios Detalhados ---

def get_detailed_report_data_db(user_id, start_date_str, end_date_str, transaction_type=None, category_id=None):
    """
    Busca dados detalhados para relatórios com base em filtros.
    Retorna dados para gráficos e um resumo para a IA.
    """
    # Validação de datas
    try:
        start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
    except ValueError:
        return {'error': 'Formato de data inválido.'}

    # Query base para transações
    transactions_query = Transaction.query.filter(
        Transaction.user_id == user_id,
        Transaction.date >= start_date_str,
        Transaction.date <= end_date_str
    )

    if transaction_type and transaction_type in ['income', 'expense']:
        transactions_query = transactions_query.filter(Transaction.type == transaction_type)
    
    if category_id:
        transactions_query = transactions_query.filter(Transaction.category_id == category_id)

    transactions = transactions_query.all()

    # 1. Dados para Gráfico de Despesas por Categoria (Pie Chart)
    expenses_by_category = {}
    total_expenses_in_period_chart = 0.0 # Total para o gráfico
    for t in transactions:
        if t.type == 'expense' and t.category:
            category_name = t.category.name
            expenses_by_category[category_name] = expenses_by_category.get(category_name, 0) + t.amount
            total_expenses_in_period_chart += t.amount
    
    expenses_chart_data = {
        'labels': list(expenses_by_category.keys()),
        'values': list(expenses_by_category.values())
    }

    # 2. Dados para Gráfico de Evolução do Patrimônio Líquido (Line Chart)
    net_worth_labels = []
    net_worth_values = []

    # Pega todas as transações do usuário até a data final do relatório, ordenadas por data
    all_user_transactions_until_end_date = Transaction.query.filter(
        Transaction.user_id == user_id,
        Transaction.date <= end_date_str
    ).order_by(Transaction.date.asc()).all()

    # Calcula o saldo inicial no dia anterior ao início do relatório
    current_net_worth = 0.0
    for t in all_user_transactions_until_end_date:
        t_date = datetime.datetime.strptime(t.date, '%Y-%m-%d').date()
        if t_date < start_date:
            if t.type == 'income':
                current_net_worth += t.amount
            else:
                current_net_worth -= t.amount
    
    # Itera pelos dias dentro do período do relatório para construir a evolução
    current_date_iter = start_date
    while current_date_iter <= end_date:
        net_worth_labels.append(current_date_iter.strftime('%d/%m/%Y'))
        
        # Adiciona o efeito das transações que ocorreram neste dia
        for t in all_user_transactions_until_end_date:
            t_date = datetime.datetime.strptime(t.date, '%Y-%m-%d').date()
            if t_date == current_date_iter:
                if t.type == 'income':
                    current_net_worth += t.amount
                else:
                    current_net_worth -= t.amount
        net_worth_values.append(current_net_worth)
        current_date_iter += datetime.timedelta(days=1)

    net_worth_chart_data = {
        'labels': net_worth_labels,
        'values': net_worth_values
    }

    # 3. Resumo para IA
    total_income_in_period = sum(t.amount for t in transactions if t.type == 'income')
    total_expenses_in_period_for_ai = sum(t.amount for t in transactions if t.type == 'expense')
    balance_in_period = total_income_in_period - total_expenses_in_period_for_ai

    ai_summary_data = {
        'start_date': start_date_str,
        'end_date': end_date_str,
        'total_income': total_income_in_period,
        'total_expenses': total_expenses_in_period_for_ai,
        'balance': balance_in_period,
        'expenses_by_category': expenses_by_category,
        'transaction_count': len(transactions)
    }

    return {
        'expenses_by_category_chart': expenses_chart_data,
        'net_worth_evolution_chart': net_worth_chart_data,
        'ai_summary': ai_summary_data
    }

# --- Funções de E-mail ---

def generate_recovery_code(length=6):
    """Gera um código alfanumérico aleatório para recuperação de senha."""
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for i in range(length))

def send_recovery_email(recipient_email, recovery_code):
    """Envia o código de recuperação para o e-mail do usuário."""
    if not Config.EMAIL_USERNAME or not Config.EMAIL_PASSWORD:
        print("ERROR: send_recovery_email - Credenciais de email (EMAIL_USERNAME ou EMAIL_PASSWORD) não configuradas. Verifique as variáveis de ambiente.")
        return False

    sender_email = Config.EMAIL_USERNAME
    sender_password = Config.EMAIL_PASSWORD
    smtp_server = Config.EMAIL_SERVER
    smtp_port = Config.EMAIL_PORT

    message = MIMEText(
        f"Seu código de recuperação de senha é: {recovery_code}\n"
        "Este código é válido por 10 minutos. Se você não solicitou esta redefinição, por favor, ignore este e-mail."
    )
    message["Subject"] = "Código de Recuperação de Senha - Gestão Financeira Pessoal"
    message["From"] = sender_email
    message["To"] = recipient_email
    
    try:
        print(f"DEBUG: send_recovery_email - Tentando conectar a {smtp_server}:{smtp_port} com usuário {sender_email}")
        context = ssl.create_default_context()
        
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls(context=context)
            server.login(sender_email, sender_password)
            print(f"DEBUG: send_recovery_email - Login SMTP bem-sucedido para {sender_email}")
            server.sendmail(sender_email, recipient_email, message.as_string())
        
        print(f"DEBUG: send_recovery_email - Email de recuperação enviado para {recipient_email}")
        return True
    except smtplib.SMTPAuthenticationError as auth_err:
        print(f"ERROR: send_recovery_email - SMTPAuthenticationError: Falha de autenticação. Verifique o EMAIL_USERNAME e a SENHA DE APLICAÇÃO (App Password) no Render. Erro: {auth_err}")
        return False
    except smtplib.SMTPServerDisconnected as disconnect_err:
        print(f"ERROR: send_recovery_email - SMTPServerDisconnected: Servidor desconectado inesperadamente. Verifique EMAIL_SERVER e EMAIL_PORT. Erro: {disconnect_err}")
        return False
    except smtplib.SMTPException as smtp_err:
        print(f"ERROR: send_recovery_email - SMTPException: Erro geral SMTP. Pode ser problema de conexão, firewall ou servidor SMTP. Erro: {smtp_err}")
        return False
    except Exception as e:
        print(f"ERROR: send_recovery_email - Erro inesperado ao enviar email: {type(e).__name__} - {e}")
        return False

# --- Funções Gemini (IA) ---

def generate_text_with_gemini(prompt_text):
    """
    Chama a API do Gemini para gerar texto com base em um prompt.
    """
    try:
        # Configura a API Key antes de usar o modelo
        genai.configure(api_key=Config.GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt_text)
        return response.text
    except Exception as e:
        print(f"ERROR: Erro ao chamar Gemini API: {e}")
        return "Não foi possível gerar uma sugestão/resumo no momento. Verifique sua chave de API e conexão."

# --- Funções de Criação de Dados Padrão ---

def create_default_data_for_user(user):
    """Cria categorias e uma conta padrão para um novo usuário."""
    # Cria categorias padrão
    db.session.add(Category(name='Salário', type='income', user_id=user.id))
    db.session.add(Category(name='Freelance', type='income', user_id=user.id))
    db.session.add(Category(name='Outras Receitas', type='income', user_id=user.id))
    db.session.add(Category(name='Alimentação', type='expense', user_id=user.id))
    db.session.add(Category(name='Transporte', type='expense', user_id=user.id))
    db.session.add(Category(name='Lazer', type='expense', user_id=user.id))
    db.session.add(Category(name='Moradia', type='expense', user_id=user.id))
    db.session.add(Category(name='Saúde', type='expense', user_id=user.id))
    db.session.add(Category(name='Educação', type='expense', user_id=user.id))
    db.session.add(Category(name='Contas Fixas', type='expense', user_id=user.id))
    db.session.add(Category(name='Outras Despesas', type='expense', user_id=user.id))
    db.session.add(Category(name='Poupança para Metas', type='expense', user_id=user.id))
    
    # Cria uma conta principal padrão
    db.session.add(Account(name='Conta Principal', balance=0.00, user_id=user.id))
    
    db.session.commit()
    print(f"Categorias e conta padrão criadas para o usuário '{user.email}'.")

