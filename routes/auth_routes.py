from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_user, logout_user, login_required, current_user
import datetime
import calendar # Adicione esta linha

from services import (
    create_default_data_for_user, generate_recovery_code, send_recovery_email,
    generate_text_with_gemini
)
from models import db, User, Transaction, Category

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']

        if len(password) < 8:
            flash('A senha deve ter no mínimo 8 caracteres.', 'danger')
            return redirect(url_for('auth.register'))

        existing_user_email = User.query.filter_by(email=email).first()
        existing_user_username = User.query.filter_by(username=username).first()

        if existing_user_email:
            flash('Este e-mail já está em uso. Por favor, escolha outro.', 'danger')
        elif existing_user_username:
            flash('Este nome de usuário já está em uso. Por favor, escolha outro.', 'danger')
        else:
            new_user = User(email=email, username=username)
            new_user.set_password(password)
            db.session.add(new_user)
            db.session.commit()

            create_default_data_for_user(new_user)

            flash('Conta criada com sucesso! Faça login.', 'success')
            return redirect(url_for('auth.login'))
    return render_template('register.html')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        identifier = request.form['identifier']
        password = request.form['password']

        user = User.query.filter((User.email == identifier) | (User.username == identifier)).first()
        if user and user.check_password(password):
            login_user(user)
            flash('Login realizado com sucesso!', 'success')
            return redirect(url_for('main.index'))
        else:
            flash('E-mail/Nome de usuário ou senha incorretos.', 'danger')
    return render_template('login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Você foi desconectado.', 'info')
    return redirect(url_for('auth.login'))

@auth_bp.route('/profile')
@login_required
def profile():
    return render_template('profile.html', current_user=current_user)

@auth_bp.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        old_password = request.form['old_password']
        new_password = request.form['new_password']
        confirm_new_password = request.form['confirm_new_password']

        if not current_user.check_password(old_password):
            flash('Senha antiga incorreta.', 'danger')
        elif len(new_password) < 8:
            flash('A nova senha deve ter no mínimo 8 caracteres.', 'danger')
        elif new_password != confirm_new_password:
            flash('A nova senha e a confirmação não coincidem.', 'danger')
        else:
            current_user.set_password(new_password)
            db.session.commit()
            flash('Senha alterada com sucesso!', 'success')
            return redirect(url_for('auth.profile'))
    return render_template('change_password.html')

@auth_bp.route('/delete_account_user', methods=['GET', 'POST'])
@login_required
def delete_account_user():
    if request.method == 'POST':
        confirm_password = request.form['confirm_password']

        if current_user.check_password(confirm_password):
            user_to_delete = User.query.get(current_user.id)
            if user_to_delete:
                logout_user()
                db.session.delete(user_to_delete)
                db.session.commit()
                flash('Sua conta foi excluída permanentemente.', 'success')
                return redirect(url_for('auth.register'))
            else:
                flash('Erro ao encontrar sua conta.', 'danger')
        else:
            flash('Senha incorreta.', 'danger')
    return render_template('delete_account.html')

@auth_bp.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password_request():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        email = request.form['email']
        user = User.query.filter_by(email=email).first()

        if user:
            recovery_code = generate_recovery_code()
            user.recovery_code = recovery_code
            user.recovery_code_expires_at = datetime.datetime.now() + datetime.timedelta(minutes=10)
            db.session.commit()

            if send_recovery_email(user.email, recovery_code):
                flash('Um código de recuperação foi enviado para o seu e-mail.', 'success')
                return redirect(url_for('auth.forgot_password_request', code_sent='true', email=email))
            else:
                flash('Não foi possível enviar o código de recuperação. Verifique suas configurações de e-mail.', 'danger')
        else:
            flash('E-mail não encontrado.', 'danger')
        
    return render_template('forgot_password.html')

@auth_bp.route('/reset_password_verify', methods=['POST'])
def reset_password_verify():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    email = request.form['email']
    code = request.form['code']
    new_password = request.form['new_password']
    confirm_new_password = request.form['confirm_new_password']

    user = User.query.filter_by(email=email).first()

    if not user:
        flash('E-mail não encontrado.', 'danger')
    elif user.recovery_code is None or user.recovery_code_expires_at is None:
        flash('Não há solicitação de recuperação de senha ativa para este e-mail. Solicite um novo código.', 'danger')
    elif user.recovery_code != code.upper():
        flash('Código de recuperação incorreto.', 'danger')
    elif datetime.datetime.now() > user.recovery_code_expires_at:
        flash('O código de recuperação expirou. Solicite um novo código.', 'danger')
    elif len(new_password) < 8:
        flash('A nova senha deve ter no mínimo 8 caracteres.', 'danger')
    elif new_password != confirm_new_password:
        flash('A nova senha e a confirmação não coincidem.', 'danger')
    else:
        user.set_password(new_password)
        user.recovery_code = None
        user.recovery_code_expires_at = None
        db.session.commit()
        flash('Sua senha foi redefinida com sucesso! Faça login.', 'success')
        return redirect(url_for('auth.login'))
        
    return render_template('forgot_password.html', code_sent='true', email=email)

@auth_bp.route('/profile/update_picture', methods=['POST'])
@login_required
def update_profile_picture():
    picture_url = request.form['profile_picture_url']
    current_user.profile_picture_url = picture_url
    db.session.commit()
    flash('Foto de perfil atualizada com sucesso!', 'success')
    return redirect(url_for('auth.profile'))

@auth_bp.route('/profile/monthly_summary', methods=['GET'])
@login_required
def get_monthly_summary():
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)

    if not year or not month:
        current_date = datetime.date.today()
        year = current_date.year
        month = current_date.month

    start_date = datetime.date(year, month, 1)
    end_date = start_date.replace(day=calendar.monthrange(year, month)[1])
    
    start_date_str = start_date.isoformat()
    end_date_query_str = (end_date + datetime.timedelta(days=1)).isoformat()

    monthly_transactions = Transaction.query.filter(
        Transaction.user_id == current_user.id,
        Transaction.date >= start_date_str,
        Transaction.date < end_date_query_str
    ).all()

    monthly_income = sum(t.amount for t in monthly_transactions if t.type == 'income')
    monthly_expenses = sum(t.amount for t in monthly_transactions if t.type == 'expense')
    monthly_balance = monthly_income - monthly_expenses
    
    transactions_details = [
        {'description': t.description, 'amount': t.amount, 'type': t.type, 'date': t.date,
         'category': t.category.name if t.category else 'Sem Categoria'}
        for t in monthly_transactions
    ]
    
    return jsonify({
        'year': year,
        'month': month,
        'income': monthly_income,
        'expenses': monthly_expenses,
        'balance': monthly_balance,
        'transactions_details': transactions_details
    })

@auth_bp.route('/get_chart_data', methods=['GET'])
@login_required
def get_chart_data():
    user_id = current_user.id
    
    today = datetime.date.today()
    
    month_labels = []
    monthly_income_data = {}
    monthly_expenses_data = {}

    for i in range(6, -1, -1):
        target_month_date = today - datetime.timedelta(days=30 * i)
        target_month = target_month_date.month
        target_year = target_month_date.year
        
        first_day_of_month = datetime.date(target_year, target_month, 1)
        last_day_of_month = first_day_of_month.replace(day=calendar.monthrange(target_year, target_month)[1])

        month_name = first_day_of_month.strftime('%b/%Y')
        month_labels.append(month_name)
        
        start_date_str = first_day_of_month.isoformat()
        end_date_str = (last_day_of_month + datetime.timedelta(days=1)).isoformat()

        transactions_in_month = Transaction.query.filter(
            Transaction.user_id == user_id,
            Transaction.date >= start_date_str,
            Transaction.date < end_date_str
        ).all()
        
        monthly_income_data[month_name] = sum(t.amount for t in transactions_in_month if t.type == 'income')
        monthly_expenses_data[month_name] = sum(t.amount for t in transactions_in_month if t.type == 'expense')

    monthly_overview_chart_data = {
        'labels': month_labels,
        'income': [monthly_income_data[m] for m in month_labels],
        'expenses': [monthly_expenses_data[m] for m in month_labels]
    }

    expenses_by_category = {}
    
    current_year_start = today.replace(month=1, day=1)
    current_year_end = today.replace(month=12, day=31)

    start_year_str = current_year_start.isoformat()
    end_year_str = (current_year_end + datetime.timedelta(days=1)).isoformat()

    current_year_transactions = Transaction.query.filter(
        Transaction.user_id == user_id,
        Transaction.type == 'expense',
        Transaction.date >= start_year_str,
        Transaction.date < end_year_str
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

@auth_bp.route('/ai_insight', methods=['POST'])
@login_required
def get_ai_insight():
    data = request.get_json()
    
    summary_data = data.get('summary_data') 
    report_data = data.get('report_data')

    prompt_parts = []
    
    if summary_data:
        prompt_parts.extend([
            f"Com base nos seguintes dados financeiros de um mês específico para o usuário {current_user.username}:",
            f"Receita Total: R${summary_data['income']:.2f},",
            f"Despesa Total: R${summary_data['expenses']:.2f},",
            f"Saldo Mensal: R${summary_data['balance']:.2f}."
        ])
        if summary_data.get('transactions_details'):
            limited_transactions = summary_data['transactions_details'][:5]
            trans_str = ", ".join([f"{t['description']} (R${t['amount']:.2f}, {t['type']}, {t['category']})" for t in limited_transactions])
            prompt_parts.append(f"Principais transações: {trans_str}.")

    elif report_data:
        start_date = report_data.get('start_date', 'N/A')
        end_date = report_data.get('end_date', 'N/A')
        total_income = report_data.get('total_income', 0.0)
        total_expenses = report_data.get('total_expenses', 0.0)
        balance = report_data.get('balance', 0.0)
        expenses_by_category = report_data.get('expenses_by_category', {})
        transaction_count = report_data.get('transaction_count', 0)

        prompt_parts.extend([
            f"Com base nos dados financeiros do usuário {current_user.username} para o período de {start_date} a {end_date}:",
            f"Receita Total: R${total_income:.2f},",
            f"Despesa Total: R${total_expenses:.2f},",
            f"Saldo Líquido no Período: R${balance:.2f}.",
            f"Total de {transaction_count} transações registradas."
        ])
        if expenses_by_category:
            expense_cat_str = ", ".join([f"{cat}: R${val:.2f}" for cat, val in expenses_by_category.items()])
            prompt_parts.append(f"Despesas por Categoria: {expense_cat_str}.")
        
    else:
        return jsonify({'error': 'Dados de resumo ou relatório não fornecidos.'}), 400

    prompt_parts.append(
        f"Forneça um breve insight ou conselho financeiro pessoal para o usuário. "
        f"Concentre-se em pontos fortes, áreas para melhoria ou tendências. "
        f"Use uma linguagem amigável e direta em português. "
        f"Seja conciso, com no máximo 150 palavras (pode exceder ligeiramente se necessário para clareza). "
        f"Não inclua 'Olá!' ou saudações, vá direto ao ponto."
    )

    prompt_text = " ".join(prompt_parts)
    ai_text = generate_text_with_gemini(prompt_text)
    return jsonify({'insight': ai_text})
