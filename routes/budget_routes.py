from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
import datetime
from dateutil.relativedelta import relativedelta # Para cálculo de datas
import json

# Importa as funções de serviço e os modelos
from services import (
    get_current_month_year_str, get_month_start_end_dates,
    add_budget_db, edit_budget_db, delete_budget_db,
    generate_text_with_gemini
)
from models import db, Budget, Category, Transaction # Importa db para commits diretos

budget_bp = Blueprint('budget', __name__)

@budget_bp.route('/budgets')
@login_required
def budgets_page():
    """Exibe a página de gerenciamento de orçamentos."""
    user_id = current_user.id
    
    # Obtém o mês/ano selecionado na URL ou o mês/ano atual por padrão
    selected_month_year = request.args.get('month_year', get_current_month_year_str())
    
    # Busca os orçamentos para o mês/ano selecionado
    budgets = Budget.query.filter_by(user_id=user_id, month_year=selected_month_year).all()
    
    # Busca todas as categorias de despesa para o formulário de adição
    expense_categories = Category.query.filter_by(user_id=user_id, type='expense').all()

    # Recalcula o 'current_spent' para cada orçamento antes de renderizar
    start_date_obj, end_date_obj = get_month_start_end_dates(selected_month_year)
    start_date_str = start_date_obj.isoformat()
    end_date_str = end_date_obj.isoformat()

    for budget in budgets:
        total_spent_in_category = db.session.query(db.func.sum(Transaction.amount)).filter(
            Transaction.user_id == user_id,
            Transaction.category_id == budget.category_id,
            Transaction.type == 'expense',
            Transaction.date >= start_date_str,
            Transaction.date <= end_date_str
        ).scalar() or 0.0
        budget.current_spent = total_spent_in_category
        db.session.add(budget) # Marca para salvar as atualizações de current_spent
    db.session.commit() # Salva todas as atualizações de current_spent

    # Calcula o mês anterior e próximo para a navegação
    current_date_for_nav = datetime.datetime.strptime(selected_month_year + '-01', '%Y-%m-%d').date()
    prev_month = (current_date_for_nav - relativedelta(months=1)).strftime('%Y-%m')
    next_month = (current_date_for_nav + relativedelta(months=1)).strftime('%Y-%m')

    return render_template('budgets.html', 
                           budgets=budgets, 
                           expense_categories=expense_categories, 
                           current_month_year=selected_month_year,
                           prev_month_year=prev_month,
                           next_month_year=next_month)

@budget_bp.route('/add_budget', methods=['POST'])
@login_required
def handle_add_budget():
    """Lida com a adição de um novo orçamento."""
    category_id = request.form['category_id']
    budget_amount = request.form['budget_amount']
    month_year = request.form['month_year']

    if add_budget_db(current_user.id, category_id, budget_amount, month_year):
        flash('Orçamento adicionado/atualizado com sucesso!', 'success')
    else:
        flash('Erro ao adicionar/atualizar orçamento. Um orçamento para esta categoria e mês já pode existir.', 'danger')
    return redirect(url_for('budget.budgets_page', month_year=month_year))

@budget_bp.route('/edit_budget/<int:budget_id>', methods=['POST'])
@login_required
def handle_edit_budget(budget_id):
    """Lida com a edição de um orçamento existente."""
    budget_amount = request.form['budget_amount']
    if edit_budget_db(budget_id, current_user.id, budget_amount):
        flash('Orçamento atualizado com sucesso!', 'success')
    else:
        flash('Erro ao atualizar orçamento.', 'danger')
    
    # Redireciona para o mês correto após a edição
    budget = Budget.query.get(budget_id)
    redirect_month_year = budget.month_year if budget else get_current_month_year_str()
    return redirect(url_for('budget.budgets_page', month_year=redirect_month_year))

@budget_bp.route('/delete_budget/<int:budget_id>', methods=['POST'])
@login_required
def handle_delete_budget(budget_id):
    """Lida com a exclusão de um orçamento."""
    budget = Budget.query.get(budget_id) # Pega o orçamento antes de deletar para o redirect
    redirect_month_year = budget.month_year if budget else get_current_month_year_str()

    if delete_budget_db(budget_id, current_user.id):
        flash('Orçamento excluído com sucesso!', 'info')
    else:
        flash('Erro ao excluir orçamento.', 'danger')
    
    return redirect(url_for('budget.budgets_page', month_year=redirect_month_year))

@budget_bp.route('/recreate_last_month_budget')
@login_required
def recreate_last_month_budget():
    """Recria os orçamentos do mês anterior para o mês atual."""
    current_month_date = datetime.date.today().replace(day=1)
    last_month_date = current_month_date - relativedelta(months=1)
    last_month_year = last_month_date.strftime('%Y-%m')
    current_month_year = current_month_date.strftime('%Y-%m')

    last_month_budgets = Budget.query.filter_by(user_id=current_user.id, month_year=last_month_year).all()

    if not last_month_budgets:
        flash('Nenhum orçamento encontrado no mês anterior para copiar.', 'warning')
        return redirect(url_for('budget.budgets_page'))

    for budget in last_month_budgets:
        # Tenta adicionar, se já existir para a categoria/mês, ele atualiza
        add_budget_db(current_user.id, budget.category_id, budget.budget_amount, current_month_year)
    
    flash('Orçamento do mês anterior recriado com sucesso!', 'success')
    return redirect(url_for('budget.budgets_page', month_year=current_month_year))

@budget_bp.route('/suggest_budget_with_ai')
@login_required
def suggest_budget_with_ai():
    """Sugere um orçamento para o mês atual usando a IA com base nos gastos anteriores."""
    current_month_date = datetime.date.today().replace(day=1)
    last_month_date = current_month_date - relativedelta(months=1)
    last_month_year = last_month_date.strftime('%Y-%m')
    current_month_year = current_month_date.strftime('%Y-%m')

    last_month_budgets = Budget.query.filter_by(user_id=current_user.id, month_year=last_month_year).all()

    if not last_month_budgets:
        flash('Nenhum orçamento encontrado no mês anterior para usar como base para a sugestão da IA.', 'warning')
        return redirect(url_for('budget.budgets_page'))

    # Coleta dados de gastos reais do mês anterior para o prompt da IA
    budget_vs_actual = []
    for budget in last_month_budgets:
        budget_vs_actual.append(
            f"- Categoria: {budget.category.name}, Orçado: R${budget.budget_amount:.2f}, Gasto Real: R${budget.current_spent:.2f}"
        )
    
    data_summary = "\n".join(budget_vs_actual)

    prompt = (
        f"Você é um assistente financeiro. Com base nos seguintes dados financeiros de um mês específico para o usuário {current_user.username}:"
        f"sugira um novo orçamento para o mês atual. Ajuste os valores de forma realista. "
        f"Se o gasto foi muito maior que o orçado, sugira um aumento moderado. Se foi muito menor, sugira uma pequena redução. "
        f"Mantenha os valores arredondados para facilitar. Responda APENAS com um JSON contendo uma chave 'sugestoes' que é uma lista de objetos, "
        f"onde cada objeto tem as chaves 'categoria' e 'valor_sugerido'.\n\n"
        f"Dados do Mês Anterior:\n{data_summary}"
    )

    try:
        ai_response_text = generate_text_with_gemini(prompt).replace("```json", "").replace("```", "").strip()
        suggestions = json.loads(ai_response_text)

        if 'sugestoes' not in suggestions:
            raise ValueError("Resposta da IA não contém a chave 'sugestoes'.")

        for sug in suggestions['sugestoes']:
            # Encontra a categoria pelo nome e tipo 'expense' para o usuário atual
            category = Category.query.filter_by(user_id=current_user.id, name=sug['categoria'], type='expense').first()
            if category:
                # Adiciona ou atualiza o orçamento com o valor sugerido pela IA
                add_budget_db(current_user.id, category.id, sug['valor_sugerido'], current_month_year)
        
        flash('Orçamento sugerido pela IA foi criado! Revise e ajuste se necessário.', 'success')
    except Exception as e:
        print(f"ERROR: Erro ao processar sugestão da IA: {e}")
        flash('Não foi possível gerar uma sugestão da IA no momento. Por favor, tente novamente mais tarde.', 'danger')

    return redirect(url_for('budget.budgets_page'))

