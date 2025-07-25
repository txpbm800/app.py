from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

# Importa as funções de serviço e os modelos
from services import add_goal_db, edit_goal_db, delete_goal_db, contribute_to_goal_db
from models import Goal, Account # Importa Account para passar para o template

goal_bp = Blueprint('goal', __name__)

@goal_bp.route('/goals', methods=['GET'])
@login_required
def goals_page():
    """Exibe a página de gerenciamento de metas financeiras."""
    goals = Goal.query.filter_by(user_id=current_user.id).all()
    user_accounts = Account.query.filter_by(user_id=current_user.id).all()
    # Converte contas para JSON para passar para o JavaScript no template
    accounts_json = [{'id': acc.id, 'name': acc.name, 'balance': acc.balance} for acc in user_accounts]
    return render_template('goals.html', goals=goals, accounts=user_accounts, accounts_json=accounts_json)

@goal_bp.route('/add_goal', methods=['POST'])
@login_required
def handle_add_goal():
    """Lida com a adição de uma nova meta."""
    name = request.form['name']
    target_amount = request.form['target_amount']
    due_date = request.form['due_date'] if request.form['due_date'] else None

    if add_goal_db(current_user.id, name, target_amount, due_date):
        flash('Meta adicionada com sucesso!', 'success')
    else:
        flash('Erro ao adicionar meta.', 'danger')
    return redirect(url_for('goal.goals_page'))

@goal_bp.route('/edit_goal/<int:goal_id>', methods=['POST'])
@login_required
def handle_edit_goal(goal_id):
    """Lida com a edição de uma meta existente."""
    name = request.form['name']
    target_amount = request.form['target_amount']
    current_amount = request.form['current_amount']
    due_date = request.form['due_date'] if request.form['due_date'] else None
    status = request.form['status']

    if edit_goal_db(goal_id, current_user.id, name, target_amount, current_amount, due_date, status):
        flash('Meta atualizada com sucesso!', 'success')
    else:
        flash('Erro ao atualizar meta.', 'danger')
    return redirect(url_for('goal.goals_page'))

@goal_bp.route('/delete_goal/<int:goal_id>', methods=['POST'])
@login_required
def handle_delete_goal(goal_id):
    """Lida com a exclusão de uma meta."""
    if delete_goal_db(goal_id, current_user.id):
        flash('Meta excluída com sucesso!', 'info')
    else:
        flash('Erro ao excluir meta.', 'danger')
    return redirect(url_for('goal.goals_page'))

@goal_bp.route('/contribute_to_goal/<int:goal_id>', methods=['POST'])
@login_required
def handle_contribute_to_goal(goal_id):
    """Lida com a contribuição para uma meta."""
    amount = request.form['amount']
    source_account_id = request.form.get('source_account_id', type=int)

    if not source_account_id:
        flash('Selecione uma conta de origem para a contribuição.', 'danger')
        return redirect(url_for('goal.goals_page'))

    # A função contribute_to_goal_db já lida com as mensagens flash
    contribute_to_goal_db(goal_id, current_user.id, amount, source_account_id)
    
    return redirect(url_for('goal.goals_page'))

