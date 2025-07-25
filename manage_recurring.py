import datetime
from dateutil.relativedelta import relativedelta
import calendar # Importar calendar aqui também, pois é usado na lógica de datas


def process_subscriptions_and_generate_transactions(user_id, db_instance, TransactionModel, SubscriptionModel, AccountModel, CategoryModel, current_date):
    """
    Processa as assinaturas ativas de um usuário, gerando transações para aquelas
    cuja próxima data de vencimento já passou ou é o dia atual.
    
    Argumentos:
        user_id (int): O ID do usuário.
        db_instance: A instância do SQLAlchemy 'db' do seu aplicativo Flask.
        TransactionModel: O modelo de banco de dados Transaction.
        SubscriptionModel: O modelo de banco de dados Subscription.
        AccountModel: O modelo de banco de dados Account.
        CategoryModel: O modelo de banco de dados Category.
        current_date (datetime.date): A data atual para comparação.
    """
    print(f"\n--- Processando assinaturas para o usuário {user_id} ---")

    active_subscriptions = SubscriptionModel.query.filter(
        SubscriptionModel.user_id == user_id,
        SubscriptionModel.status == 'active',
        db_instance.cast(SubscriptionModel.next_due_date, db_instance.Date) <= current_date
    ).all()

    if not active_subscriptions:
        print(f"Nenhuma assinatura ativa para processar para o usuário {user_id}.")
        return

    for sub in active_subscriptions:
        print(f"  Processando assinatura: {sub.name} (ID: {sub.id}), Próximo Vencimento: {sub.next_due_date}")
        
        # Categoria padrão para assinaturas, se não houver uma específica
        category_for_sub_id = sub.category_id
        if not category_for_sub_id:
            default_cat = CategoryModel.query.filter_by(user_id=user_id, name='Assinaturas', type='expense').first()
            if not default_cat:
                default_cat = CategoryModel.query.filter_by(user_id=user_id, name='Outras Despesas', type='expense').first()
            if default_cat:
                category_for_sub_id = default_cat.id
            else:
                print(f"AVISO: Nenhuma categoria 'Assinaturas' ou 'Outras Despesas' encontrada para o usuário {user_id}. Transação pode ficar sem categoria.")
                category_for_sub_id = None

        # Conta padrão para assinaturas, se não houver uma específica
        account_for_sub = None
        if sub.account_id:
            account_for_sub = AccountModel.query.get(sub.account_id)
        
        if not account_for_sub:
            # Tenta encontrar a "Conta Principal" do usuário
            account_for_sub = AccountModel.query.filter_by(user_id=user_id, name='Conta Principal').first()
            if not account_for_sub:
                # Pega a primeira conta disponível se não houver "Conta Principal"
                account_for_sub = AccountModel.query.filter_by(user_id=user_id).first()
        
        if not account_for_sub:
            print(f"ERRO: Nenhuma conta encontrada para debitar a assinatura '{sub.name}'. Pulando a geração da transação.")
            continue # Pula para a próxima assinatura se não houver conta para debitar

        # Verifica se já existe uma transação para esta assinatura na data de vencimento
        existing_transaction = TransactionModel.query.filter(
            TransactionModel.user_id == user_id,
            TransactionModel.description == f"Pagamento Assinatura: {sub.name}",
            TransactionModel.date == sub.next_due_date,
            TransactionModel.amount == sub.amount,
            TransactionModel.type == 'expense'
        ).first()

        if existing_transaction:
            print(f"  Transação para '{sub.name}' em {sub.next_due_date} já existe. Pulando a geração.")
        else:
            # Gerar a transação
            new_transaction = TransactionModel(
                description=f"Pagamento Assinatura: {sub.name}",
                amount=sub.amount,
                date=sub.next_due_date, # Usa a data de vencimento da assinatura
                type='expense',
                user_id=user_id,
                category_id=category_for_sub_id,
                account_id=account_for_sub.id
            )
            db_instance.session.add(new_transaction)
            
            # Atualizar o saldo da conta
            account_for_sub.balance -= sub.amount
            db_instance.session.add(account_for_sub)
            print(f"  Transação gerada para '{sub.name}' em {sub.next_due_date}. Saldo da conta '{account_for_sub.name}' atualizado para R${account_for_sub.balance:.2f}.")

        # Calcular a próxima data de vencimento para a assinatura
        current_next_due_date_obj = datetime.datetime.strptime(sub.next_due_date, '%Y-%m-%d').date()
        
        # Avança a data até que seja maior que a data atual
        while current_next_due_date_obj <= current_date:
            if sub.billing_cycle == 'monthly':
                current_next_due_date_obj += relativedelta(months=1)
            elif sub.billing_cycle == 'quarterly':
                current_next_due_date_obj += relativedelta(months=3)
            elif sub.billing_cycle == 'semi-annually':
                current_next_due_date_obj += relativedelta(months=6)
            elif sub.billing_cycle == 'annually':
                current_next_due_date_obj += relativedelta(years=1)
            
            # Ajusta o dia para o due_date_of_month, se possível
            try:
                current_next_due_date_obj = current_next_due_date_obj.replace(day=sub.due_date_of_month)
            except ValueError:
                # Se o dia do mês for maior que o número de dias no mês, usa o último dia do mês
                last_day_of_month = calendar.monthrange(current_next_due_date_obj.year, current_next_due_date_obj.month)[1]
                current_next_due_date_obj = current_next_due_date_obj.replace(day=last_day_of_month)

        sub.next_due_date = current_next_due_date_obj.isoformat()
        db_instance.session.add(sub)
        print(f"  Próximo vencimento para '{sub.name}' atualizado para: {sub.next_due_date}")

    db_instance.session.commit()
    print(f"--- Processamento de assinaturas concluído para o usuário {user_id} ---")
