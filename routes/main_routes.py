from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
import datetime
import calendar

# Importa as funções de serviço e os modelos
from services import (
    process_recurring_bills_on_access, get_dashboard_data_db,
    add_transaction_db, edit_transaction_db, delete_transaction_db,
    add_bill_db, pay_bill_db, reschedule_bill_db, delete_bill_db, edit_bill_db,
    get_current_month_year_str
)
from models import Transaction, Bill, Category, Account, Goal, Budget 

main_bp = Blueprint('main', __name__)

TODAY_DATE = datetime.date.today()

@main_bp.route('/')
@login_required
def index():
    """Rota principal do dashboard."""
    # Processa contas recorrentes pendentes ao acessar o dashboard
    process_recurring_bills_on_access(current_user.id)
    
    dashboard_data = get_dashboard_data_db(current_user.id)
    
    # Lógica de filtragem e ordenação de transações
    transactions_query_obj = Transaction.query.filter_by(user_id=current_user.id) 

    transaction_type_filter = request.args.get('transaction_type')
    if transaction_type_filter and transaction_type_filter in ['income', 'expense']:
        transactions_query_obj = transactions_query_obj.filter_by(type=transaction_type_filter)

    start_date_filter = request.args.get('start_date')
    end_date_filter = request.args.get('end_date')
    if start_date_filter:
        transactions_query_obj = transactions_query_obj.filter(Transaction.date >= start_date_filter)
    if end_date_filter:
        transactions_query_obj = transactions_query_obj.filter(Transaction.date <= end_date_filter)

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
            
    all_transactions = transactions_query_obj.all()
    
    income_transactions = [t for t in all_transactions if t.type == 'income']
    expense_transactions = [t for t in all_transactions if t.type == 'expense']

    # Lógica de filtragem de contas a pagar
    bills_query_obj = Bill.query.filter( 
        Bill.user_id == current_user.id,
        Bill.is_master_recurring_bill == False # Apenas contas "filhas" ou não recorrentes
    )
    bill_status_filter = request.args.get('bill_status')
    if bill_status_filter and bill_status_filter in ['pending', 'paid', 'overdue']:
        if bill_status_filter == 'overdue':
            # Contas atrasadas são aquelas pendentes com data de vencimento anterior a hoje
            bills_query_obj = bills_query_obj.filter(Bill.dueDate < TODAY_DATE.isoformat(), Bill.status == 'pending')
        else:
            bills_query_obj = bills_query_obj.filter_by(status=bill_status_filter)
    else:
        # Por padrão, mostra apenas as contas pendentes
        bills_query_obj = bills_query_obj.filter_by(status='pending')
            
    filtered_bills = bills_query_obj.order_by(Bill.dueDate.asc()).all()

    # Dados para dropdowns e alertas
    all_categories_formatted = [(c.id, c.type, c.name) for c in Category.query.filter_by(user_id=current_user.id).all()]
    
    user_accounts = Account.query.filter_by(user_id=current_user.id).all()
    accounts_json = [{'id': acc.id, 'name': acc.name, 'balance': acc.balance} for acc in user_accounts]

    current_month_year = get_current_month_year_str()
    budgets_with_alerts = []
    all_budgets_for_month = Budget.query.filter_by(user_id=current_user.id, month_year=current_month_year).all()

    for budget in all_budgets_for_month:
        if budget.budget_amount > 0:
            percentage_spent = (budget.current_spent / budget.budget_amount * 100)
            
            alert_data = {
                'category_name': budget.category.name,
                'percentage_spent': round(percentage_spent)
            }

            if percentage_spent > 100:
                alert_data['status'] = 'danger'
                alert_data['message'] = f"Alerta: Você ultrapassou em {round(percentage_spent - 100)}% o orçamento para"
                budgets_with_alerts.append(alert_data)
            elif percentage_spent >= 80:
                alert_data['status'] = 'warning'
                alert_data['message'] = f"Atenção: Você já utilizou {round(percentage_spent)}% do seu orçamento para"
                budgets_with_alerts.append(alert_data)

    active_goals = Goal.query.filter_by(user_id=current_user.id, status='in_progress').order_by(Goal.name.asc()).all()
    goals_json = [{'id': goal.id, 'name': goal.name} for goal in active_goals]

    show_new_budget_alert = False
    if not all_budgets_for_month:
        show_new_budget_alert = True

    return render_template(
        'index.html',
        dashboard=dashboard_data,
        bills=filtered_bills,
        income_transactions=income_transactions,
        expense_transactions=expense_transactions,
        current_date=TODAY_DATE.isoformat(),
        current_user=current_user,
        current_transaction_type_filter=transaction_type_filter,
        current_bill_status_filter=bill_status_filter,
        current_sort_by_transactions=sort_by_transactions,
        current_order_transactions=order_transactions,
        current_start_date=start_date_filter,
        current_end_date=end_date_filter,
        all_categories=all_categories_formatted,
        current_category_filter=category_filter_id,
        accounts=user_accounts,
        accounts_json=accounts_json,
        budgets_with_alerts=budgets_with_alerts,
        active_goals=active_goals,
        goals_json=goals_json,
        show_new_budget_alert=show_new_budget_alert
    )

@main_bp.route('/add_transaction', methods=['POST'])
@login_required
def handle_add_transaction():
    """Lida com a adição de uma nova transação."""
    description = request.form['description']
    amount = request.form['amount']
    date = request.form['date']
    transaction_type = request.form['type']
    category_id = request.form.get('category_id', type=int)
    account_id = request.form.get('account_id', type=int)
    goal_id = request.form.get('goal_id', type=int) if request.form.get('goal_id') else None

    add_transaction_db(description, amount, date, transaction_type, current_user.id, category_id, account_id, goal_id)
    flash('Transação adicionada com sucesso!', 'success')
    return redirect(url_for('main.index')) # Redireciona para o blueprint 'main'

@main_bp.route('/add_bill', methods=['POST'])
@login_required
def handle_add_bill():
    """Lida com a adição de uma nova conta a pagar/receber."""
    description = request.form['bill_description']
    amount = request.form['bill_amount']
    due_date = request.form['bill_due_date']
    
    is_recurring = request.form.get('is_recurring_bill') == 'on'
    recurring_frequency = request.form.get('recurring_frequency_bill')
    recurring_total_occurrences = request.form.get('recurring_total_occurrences_bill', type=int)
    bill_type = request.form['bill_type']
    category_id = request.form.get('bill_category_id', type=int)
    account_id = None # Account ID para bills é definido apenas no pagamento

    add_bill_db(description, amount, due_date, current_user.id, 
                is_recurring, recurring_frequency, recurring_total_occurrences, bill_type, category_id, account_id)
    flash('Conta adicionada com sucesso!', 'success')
    return redirect(url_for('main.index'))

@main_bp.route('/pay_bill/<int:bill_id>', methods=['POST'])
@login_required
def handle_pay_bill(bill_id):
    """Lida com o pagamento de uma conta."""
    payment_account_id = request.form.get('payment_account_id', type=int) 
    
    if not payment_account_id:
        flash('Selecione uma conta para realizar o pagamento.', 'danger')
        return redirect(url_for('main.index'))

    if pay_bill_db(bill_id, current_user.id, payment_account_id):
        flash('Conta paga e transação registrada com sucesso!', 'success')
    # A função pay_bill_db já lida com saldo insuficiente e outras validações,
    # gerando a flash message apropriada.
    return redirect(url_for('main.index'))

@main_bp.route('/reschedule_bill/<int:bill_id>', methods=['POST'])
@login_required
def handle_reschedule_bill(bill_id):
    """Lida com o reagendamento de uma conta."""
    new_date = request.form['new_date']
    if reschedule_bill_db(bill_id, new_date, current_user.id):
        flash('Conta remarcada com sucesso!', 'success')
    else:
        flash('Não foi possível remarcar a conta. Verifique se ela existe ou pertence a você.', 'danger')
    return redirect(url_for('main.index'))

@main_bp.route('/delete_transaction/<int:transaction_id>', methods=['POST'])
@login_required
def handle_delete_transaction(transaction_id):
    """Lida com a exclusão de uma transação."""
    if delete_transaction_db(transaction_id, current_user.id):
        flash('Transação excluída com sucesso!', 'info')
    else:
        flash('Não foi possível excluir a transação. Verifique se ela existe ou pertence a você.', 'danger')
    return redirect(url_for('main.index'))

@main_bp.route('/delete_bill/<int:bill_id>', methods=['POST'])
@login_required
def handle_delete_bill(bill_id):
    """Lida com a exclusão de uma conta."""
    if delete_bill_db(bill_id, current_user.id):
        flash('Conta excluída com sucesso!', 'info')
    else:
        flash('Não foi possível excluir a conta. Verifique se ela existe ou pertence a você.', 'danger')
    return redirect(url_for('main.index'))

@main_bp.route('/get_transaction_data/<int:transaction_id>', methods=['GET'])
@login_required
def get_transaction_data(transaction_id):
    """Retorna os dados de uma transação específica para edição via AJAX."""
    transaction = Transaction.query.filter_by(id=transaction_id, user_id=current_user.id).first()
    if transaction:
        return jsonify({
            'id': transaction.id,
            'description': transaction.description,
            'amount': transaction.amount,
            'date': transaction.date,
            'type': transaction.type,
            'category_id': transaction.category_id,
            'account_id': transaction.account_id,
            'goal_id': transaction.goal_id
        })
    return jsonify({'error': 'Transação não encontrada ou não pertence a este usuário'}), 404

@main_bp.route('/edit_transaction/<int:transaction_id>', methods=['POST'])
@login_required
def handle_edit_transaction(transaction_id):
    """Lida com a edição de uma transação."""
    description = request.form['edit_description']
    amount = request.form['edit_amount']
    date = request.form['edit_date']
    transaction_type = request.form['edit_type']
    category_id = request.form.get('edit_category_id', type=int)
    account_id = request.form.get('edit_account_id', type=int)
    goal_id = request.form.get('edit_goal_id', type=int) if request.form.get('edit_goal_id') else None

    if edit_transaction_db(transaction_id, description, amount, date, transaction_type, current_user.id, category_id, account_id, goal_id):
        flash('Transação atualizada com sucesso!', 'success')
    else:
        flash('Não foi possível atualizar a transação. Verifique se ela existe ou pertence a você.', 'danger')
    return redirect(url_for('main.index'))

@main_bp.route('/get_bill_data/<int:bill_id>', methods=['GET'])
@login_required
def get_bill_data(bill_id):
    """Retorna os dados de uma conta a pagar/receber específica para edição via AJAX."""
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
            'category_id': bill.category_id,
            'account_id': bill.account_id
        })
    return jsonify({'error': 'Conta não encontrada ou não pertence a este usuário'}), 404

@main_bp.route('/edit_bill/<int:bill_id>', methods=['POST'])
@login_required
def handle_edit_bill(bill_id):
    """Lida com a edição de uma conta a pagar/receber."""
    description = request.form['edit_bill_description']
    amount = request.form['edit_bill_amount']
    due_date = request.form['edit_bill_dueDate']
    
    # Campos de recorrência (podem não estar presentes no formulário de edição do index)
    is_recurring = request.form.get('edit_is_recurring_bill') == 'on'
    recurring_frequency = request.form.get('edit_recurring_frequency_bill')
    recurring_total_occurrences = request.form.get('edit_recurring_total_occurrences_bill', type=int)
    is_active_recurring = request.form.get('edit_is_active_recurring_bill') == 'on'
    bill_type = request.form['edit_bill_type']
    category_id = request.form.get('edit_bill_category_id', type=int)
    
    # Mantém o account_id original, pois não é editado no formulário do index
    bill = Bill.query.filter_by(id=bill_id, user_id=current_user.id).first()
    account_id = bill.account_id if bill else None

    if edit_bill_db(bill_id, description, amount, due_date, current_user.id,
                      is_recurring, recurring_frequency, recurring_total_occurrences, is_active_recurring, bill_type, category_id, account_id):
        flash('Conta atualizada com sucesso!', 'success')
    else:
        flash('Não foi possível atualizar a conta. Verifique se ela existe ou pertence a você.', 'danger')
    return redirect(url_for('main.index'))

