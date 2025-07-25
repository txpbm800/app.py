from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

# Importa as funções de serviço e os modelos
from services import add_account_db, edit_account_db, delete_account_db, transfer_funds_db
from models import Account # Importa Account para buscar dados

account_bp = Blueprint('account', __name__)

@account_bp.route('/accounts')
@login_required
def accounts_page():
    """Exibe a página de gerenciamento de contas."""
    user_accounts = Account.query.filter_by(user_id=current_user.id).order_by(Account.name.asc()).all()
    # Converte contas para JSON para passar para o JavaScript no template
    accounts_json = [{'id': acc.id, 'name': acc.name, 'balance': acc.balance} for acc in user_accounts]
    return render_template('accounts.html', accounts=user_accounts, accounts_json=accounts_json)

@account_bp.route('/add_account', methods=['POST'])
@login_required
def handle_add_account():
    """Lida com a adição de uma nova conta."""
    name = request.form['name']
    initial_balance = request.form['initial_balance']
    
    if add_account_db(current_user.id, name, initial_balance):
        flash('Conta adicionada com sucesso!', 'success')
    # A função add_account_db já lida com a flash message de erro
    return redirect(url_for('account.accounts_page'))

@account_bp.route('/edit_account/<int:account_id>', methods=['POST'])
@login_required
def handle_edit_account(account_id):
    """Lida com a edição de uma conta existente."""
    name = request.form['name']
    balance = request.form['balance']
    
    if edit_account_db(account_id, current_user.id, name, balance):
        flash('Conta atualizada com sucesso!', 'success')
    # A função edit_account_db já lida com a flash message de erro
    return redirect(url_for('account.accounts_page'))

@account_bp.route('/delete_account/<int:account_id>', methods=['POST'])
@login_required
def handle_delete_account(account_id):
    """Lida com a exclusão de uma conta."""
    if delete_account_db(account_id, current_user.id):
        flash('Conta excluída com sucesso! Transações associadas foram desvinculadas.', 'info')
    # A função delete_account_db já lida com a flash message de erro
    return redirect(url_for('account.accounts_page'))

@account_bp.route('/get_account_data/<int:account_id>', methods=['GET'])
@login_required
def get_account_data(account_id):
    """Retorna dados de uma conta específica para edição via AJAX."""
    account = Account.query.filter_by(id=account_id, user_id=current_user.id).first()
    if account:
        return jsonify({
            'id': account.id,
            'name': account.name,
            'balance': account.balance
        })
    return jsonify({'error': 'Conta não encontrada ou não pertence a este usuário'}), 404

@account_bp.route('/transfer_funds', methods=['POST'])
@login_required
def handle_transfer_funds():
    """Lida com a transferência de fundos entre contas."""
    source_account_id = request.form.get('source_account_id', type=int)
    destination_account_id = request.form.get('destination_account_id', type=int)
    amount = request.form.get('amount', type=float)

    # A função transfer_funds_db já lida com as flash messages
    transfer_funds_db(current_user.id, source_account_id, destination_account_id, amount)
    
    return redirect(url_for('account.accounts_page'))

