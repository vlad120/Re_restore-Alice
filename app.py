"""
    Version 1.1.0 (29.04.2019)
    Mironov Vladislav
"""

from flask import Flask, request
from requests import get, post, delete
from random import choice, randint
import logging
import json

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

app = Flask(__name__)
sessionStorage = {}

# SHOP_URL = "https://re_restore.ru/"
# SHOP_URL = "http://127.0.0.1:8080/"
SHOP_URL = "https://neo120.pythonanywhere.com/"

WAITING = 'waiting'


class ApiError(Exception):
    def __init__(self, message, errors):
        super().__init__(message)
        self.errors = errors


@app.route('/alice_main', methods=['POST'])
def alice_main():
    response = {
        'session': request.json['session'],
        'version': request.json['version'],
        'response': {
            'end_session': False
        }
    }

    user_id = request.json['session']['user_id']
    handle_dialog(request.json, response, user_id)
    response['response']['buttons'] = get_suggests(user_id)

    logging.info('Response: %r', request.json)

    return json.dumps(response)


# Функция ведения диалога
def handle_dialog(req, res, user_id):
    # новая сессия
    if req['session']['new'] or user_id not in sessionStorage:
        if user_id not in sessionStorage:
            logging.error('Error: {}'.format("Old user is not in sessionStorage"))
        sessionStorage[user_id] = {
            'authorization': None,
            'state': None,
            'state_value': None
        }
        res['response']['text'] = 'Здравствуйте! Чем могу помочь?'
        return

    answer = req['request']['original_utterance'].lower()
    session = sessionStorage[user_id]
    state = session['state']

    # желание окончить сессию (прощание)
    if (state == WAITING and find_command(answer, "нет", "позже", "потом", "не хочу")) or \
            (not state and find_command(answer, "пока", "до свидания", "уйди", "отстань")):
        res['response']['text'] = 'Если что, обращайтесь!'
        res['response']['end_session'] = True
        return

    # если сейчас ведется какой-либо режим
    if state:
        # ситуация, когда на вопрос типа "Могу чем-то помочь?"
        # пользователь отвечает согласием
        if state == WAITING:
            if check_agree(answer):
                res['response']['text'] = 'Давайте поконкретнее.'
                session['state'] = None
                return

        # после вопроса о желании авторизироваться
        elif state == 'authorization_question':
            if check_agree(answer):
                res['response']['text'] = 'Хорошо, для входа в личный кабинет введите с ' \
                                          'клавиатуры свой логин и пароль через пробел.'
                session['state'] = 'authorization_waiting'
            elif check_cancel(answer):
                res['response']['text'] = 'Ладно, {}'.format(can_help())
                session['state'] = WAITING
            else:
                res['response']['text'] = 'Пожалуйста, говорите конкретнее.'
            return

        # ожидание ввода логина и пароля
        elif state == 'authorization_waiting':
            try:
                data = answer.split()
                # проверка формата
                if len(data) == 2:
                    data = {'login': data[0],
                            'password': data[1]}
                    api_resp = get_shop_api_response('api/authorization/check', get, data)
                    # при успешной авторизации на сервере
                    if api_resp['success']:
                        # сохраняем ее данные в сессию с пользователем
                        session['authorization'] = data
                        res['response']['text'] = 'Вы успшно авторизировались, и теперь ' \
                                                  'Вам доступен весь функционал. Что будем делать?'
                        session['state'] = None
                    else:
                        res['response']['text'] = 'К сожалению, вы ввели неправильные данные. Попробуйте снова.'
                else:
                    # проверка на отмену
                    if check_cancel(answer):
                        res['response']['text'] = 'Ладно, {}'.format(can_help())
                        session['state'] = WAITING
                        return
                    res['response']['text'] = 'Неверный формат! Я же сказал, ' \
                                              'нужно ввести логин и пароль через пробел. Давайте заново.'
            except Exception as e:
                make_unknown_error(e, res, session)
            return

        # после показа товара
        elif state == 'goods_showed':
            # если хочет ещё
            if find_command(answer, "ещё", "еще"):
                answer = "что бы купить"
            # если хочет добавить в корзину
            if find_command(answer, special='add_to_basket'):
                if check_authorization(session, res):
                    res['response']['text'] = 'Сколько хотите добавить?'
                    session['state'] = 'adding_to_basket'
                return

        # после показа товара из поиска
        elif state == 'search_goods_showed':
            # если хочет ещё
            if find_command(answer, "ещё", "еще"):
                if len(session['state_value']) > 1:
                    try:
                        api_resp = get_shop_api_response('api/goods/{}'.format(session['state_value'][1]), get,
                                                         data={'short_with_description': True})
                        # при успешном получении товара с сервера
                        if api_resp['success']:
                            goods = api_resp['data']['goods']
                            text = 'Вот ещё: {} – {}руб.'.format(
                                goods['name'], goods['price']
                            )
                            res['response']['text'] = text
                            res['response']['card'] = {
                                "type": "BigImage",
                                "image_id": "1540737/805f7f3757eea4d5f263",
                                "title": text,
                                "description": goods['short_description'],
                                "button": {
                                    "text": "Узнать подробнее на сайте",
                                    "url": SHOP_URL + goods['full_link'].strip('/')
                                }
                            }
                            # вычитаем из списка найденного прошлый товар
                            del session['state_value'][0]
                        else:
                            raise ApiError
                    except Exception as e:
                        make_unknown_error(e, res, session)
                else:
                    res['response']['text'] = 'К сожалению, это всё, что было найдено ;('
                    session['state'] = WAITING
                    session['state_value'] = None
                return
            # если хочет добавить в корзину
            if find_command(answer, special='add_to_basket'):
                if check_authorization(session, res):
                    res['response']['text'] = 'Сколько хотите добавить?'
                    session['state'] = 'adding_to_basket'
                    session['state_value'] = session['state_value'][0]
                return

        # при добавлении товара в корзину
        elif state == 'adding_to_basket':
            # при отмене
            if check_cancel(answer):
                res['response']['text'] = 'Ну, как хотите, не будем добавлять. {}'.format(can_help())
                session['state'] = WAITING
                return
            # поиск числа в ответе
            for entity in req['request']['nlu']['entities']:
                if entity['type'] == 'YANDEX.NUMBER':
                    try:
                        data = session['authorization']
                        data['count'] = entity['value']
                        api_resp = get_shop_api_response('api/basket/{}'.format(session['state_value']),
                                                         post, data=data)
                        # при успешном добавлении
                        if api_resp['success']:
                            res['response']['text'] = 'Добавил. {}'.format(can_help())
                            session['state'] = WAITING
                            return
                        # товара недостаточно
                        elif api_resp['message'] == "Count is more then original":
                            res['response']['text'] = 'Губу, пожалуйста, закатайте обратно. ' \
                                                      'Нет у нас столько в наличии. ' \
                                                      'Максимум – {}шт'.format(api_resp['count'])
                            return
                        # товар уже в корзине
                        elif api_resp['message'] == "Goods has already added":
                            res['response']['text'] = 'Ну, вообще-то, этот товар уже у Вас в корзине.'
                            session['state'] = None  # выходим из режима
                            return
                    except Exception as e:
                        make_unknown_error(e, res, session)
                        return
            # при отсутсвии числа
            res['response']['text'] = choice(['Не понял, сколько?',
                                              'Сколько?',
                                              'Говорите конкретнее.'])
            return

        # при удалении товара из корзины
        elif state == 'removing_goods_from_basket':
            # при отмене
            if check_cancel(answer):
                res['response']['text'] = 'Как Вам угодно – пока оставим. {}'.format(can_help())
                session['state'] = WAITING
                return
            # согласие
            if check_agree(answer):
                try:
                    api_resp = get_shop_api_response('api/basket/{}'.format(session['state_value']),
                                                     delete, data=session['authorization'])
                    if api_resp['success']:
                        res['response']['text'] = 'Удалил. {}'.format(can_help())
                        session['state'] = WAITING
                    else:
                        res['response']['text'] = 'В корзине товар №{} не найден...'.format(can_help())
                        session['state'] = None
                except Exception as e:
                    make_unknown_error(e, res, session)
                return
            res['response']['text'] = choice(['Так будем удалять, или нет?',
                                              'Не понял, удаляем?',
                                              'Говорите конретнее.'])
            return

        # подтверждение заказа
        elif state == 'order_confirm':
            # при согласии
            if check_agree(answer):
                try:
                    api_resp = get_shop_api_response('api/orders/curr', post,
                                                     data=session['authorization'])
                    # при успешном размещении заказа
                    if api_resp['success']:
                        res['response']['text'] = "Заказ №{} был создан. В течение некоторого времени " \
                                                  "с Вами свяжется наш менеджер для подтверждения. " \
                                                  "Что-нибудь ещё желаете?".format(api_resp['order_id'])
                        session['state'] = WAITING
                    else:
                        raise ApiError
                except Exception as e:
                    make_unknown_error(e, res, session)
            # при отрицании
            elif check_cancel(answer):
                res['response']['text'] = "Хорошо, пока отложим заказ на потом. {}".format(can_help())
                session['state'] = WAITING
            # непонятная фраза
            else:
                res['response']['text'] = "Говорите, пожалуйста, понятнее."
            return

    # что умею
    if find_command(answer, special='what_can'):
        res['response']['text'] = ("Я - бот-помощник онлайн магазина Re_restore. "
                                   "Я могу многое. Вот перечень моих услуг:\n"
                                   "  $ Поиск товаров (по названию/описанию/артикулу)\n"
                                   "  $ Авторизация\n"
                                   "  $ Добавление товаров в корзину\n"
                                   "  $ Удаление товаров из корзины\n"
                                   "  $ Просмотр корзины\n"
                                   "  $ Создание заказов\n"
                                   "  $ Просмотр заказов")
        session['state'] = None
        return

    # желание авторизироваться (к черту безопасность!)
    if find_command(answer, special='authorization_wish'):
        if session['authorization']:
            res['response']['text'] = choice(['Вы уже авторизированы как **{}**.',
                                              '**{}**, не знаете случайно, откуда я вас знаю?...',
                                              '**{}**, мы не знакомы?']).format(
                session['authorization']['login']
            )
            session['state'] = WAITING
            return
        res['response']['text'] = 'Ок, для входа в личный кабинет введите с ' \
                                  'клавиатуры свой логин и пароль через пробел.'
        session['state'] = 'authorization_waiting'
        return

    # желание деавторизироваться
    if find_command(answer, special='de_authorization_wish'):
        if session['authorization']:
            session['authorization'] = None
            res['response']['text'] = 'Хорошо, Вы вышли из аккаунта. {}'.format(can_help())
            session['state'] = WAITING
        else:
            res['response']['text'] = choice['Но вы и не авторизированы ;)',
                                             'А вы сначала авторизируйтесь ;)',
                                             'Для начала, Вам нужно войти в личный кабинет ;)',
                                             'Но вы ведь не авторизированы ;)']
            session['state'] = None
        return

    # просмотр 1 интересного (случайного) товара
    if find_command(answer, special='interesting_goods'):
        try:
            api_resp = get_shop_api_response('api/goods/random', get)
            # при успешном получении товара с сервера
            if api_resp['success']:
                goods = api_resp['goods'][0]  # один товар
                text = 'Могу предложить Вам {} – всего за {}руб.'.format(
                    goods['name'], goods['price']
                )
                res['response']['text'] = text
                res['response']['card'] = {
                    "type": "BigImage",
                    "image_id": "1540737/805f7f3757eea4d5f263",
                    "title": text,
                    "description": goods['short_description'],
                    "button": {
                        "text": "Узнать подробнее на сайте",
                        "url": SHOP_URL + goods['full_link'].strip('/')
                    }
                }
                session['state'] = "goods_showed"
                session['state_value'] = goods['id']
            else:
                raise ApiError
        except Exception as e:
            make_unknown_error(e, res, session)
        return

    # просмотр корзины
    if find_command(answer, special='basket_ask'):
        if check_authorization(session, res):
            try:
                api_resp = get_shop_api_response('api/basket/curr', get,
                                                 data=session['authorization'])
                # при успешном получении корзины с сервера
                if api_resp['success']:
                    values = api_resp['basket']  # значения корзины - {"goods_id": count}
                    if values:
                        basket = []
                        for val in values:
                            val = list(val.items())[0]
                            api_resp = get_shop_api_response('api/goods/{}'.format(val[0]), get,
                                                             data={'short_with_description': True})
                            if api_resp['success']:
                                goods = api_resp['data']['goods']
                                goods['count'] = val[1]
                                basket.append(goods)
                        text = 'Ваша корзина:\n{}'.format("\n".join(get_goods_list(basket)))
                        text += "\n\nИтого: {} рублей".format(int(sum(g['price'] * g['count'] for g in basket)))
                    else:
                        text = 'Ваша корзина пуста.'
                        session['state_value'] = 'empty'
                    res['response']['text'] = text
                    session['state'] = 'basket_showed'
                else:
                    raise ApiError
            except Exception as e:
                make_unknown_error(e, res, session)
        return

    # просмотр заказов
    if find_command(answer, special='orders_ask'):
        if check_authorization(session, res):
            try:
                api_resp = get_shop_api_response('api/orders/user', get,
                                                 data=session['authorization'])
                if api_resp['success']:
                    orders = api_resp['orders']
                    if orders:
                        str_orders = []
                        for order in orders[:4]:
                            str_orders.append("- №{}, {}руб ({})".format(order['id'],
                                                                         int(order['total']),
                                                                         order['status']))
                        text = 'Ваши последние заказы ({}):\n{}'.format(len(str_orders),
                                                                        "\n".join(str_orders))
                    else:
                        text = 'У Вас нет заказов.'
                    res['response']['text'] = text
                    session['state'] = None
                else:
                    raise ApiError
            except Exception as e:
                make_unknown_error(e, res, session)
        return

    # запрос на создание заказа
    if find_command(answer, special='make_order_ask'):
        if check_authorization(session, res):
            try:
                api_resp = get_shop_api_response('api/basket/curr', get,
                                                 data=session['authorization'])
                # при успешном получении козины с сервера
                if api_resp['success']:
                    values = api_resp['basket']  # значения корзины - {"goods_id": count}
                    if values:
                        basket = []
                        for val in values:
                            val = list(val.items())[0]
                            api_resp = get_shop_api_response('api/goods/{}'.format(val[0]), get,
                                                             data={'short_with_description': True})
                            if api_resp['success']:
                                goods = api_resp['data']['goods']
                                goods['count'] = val[1]
                                basket.append(goods)
                        text = 'Вы хотите заказать:\n{}'.format("\n".join(get_goods_list(basket)))
                        text += "\n\nНа общую сумму: {} рублей.\nВсё верно?".format(
                            sum(g['price'] * g['count'] for g in basket)
                        )
                        session['state'] = 'order_confirm'
                    else:
                        text = 'Ваша корзина пуста. Для создания заказа сначала добавьте туда товары.'
                        session['state'] = None
                    res['response']['text'] = text
                else:
                    raise ApiError
            except Exception as e:
                make_unknown_error(e, res, session)
        return

    # запрос на удаление товара из корзины
    result = find_command(answer, special='remove_goods_from_basket_ask')
    if result:
        try:
            last = result.split()[-1]
            data = {'text': answer[answer.index(last) + len(last):]}  # выделяем часть после фразы запроса

            api_resp = get_shop_api_response('api/search/auto', get, data=data)
            if api_resp['success']:
                all_goods = api_resp['goods']  # найденные товары
                logging.info(all_goods)
                if all_goods:
                    goods = get_shop_api_response('api/goods/{}'.format(all_goods[0]), get,
                                                  data={'short': True})['data']['goods']
                    res['response']['text'] = 'Вы хотите удалить товар №{} ({})?'.format(goods['id'],
                                                                                         goods['name'])
                    session['state'] = 'removing_goods_from_basket'
                    session['state_value'] = goods['id']
                else:
                    res['response']['text'] = "Такого товара, скорее всего, не существует. " \
                                              "Говорите конкретнее".format(result)
                    session['state'] = None
            else:
                raise ApiError
        except Exception as e:
            make_unknown_error(e, res, session)
        return

    # поиск товара
    result = find_command(answer, special='search_goods')
    if result:
        try:
            api_resp = get_shop_api_response('api/search/auto', get, data={'text': result})
            if api_resp['success']:
                all_goods = api_resp['goods']  # найденные товары
                if not all_goods:
                    res['response']['text'] = "По вашему запросу ('{}') ничего не найдено".format(result)
                    session['state'] = None
                    return

                goods = get_shop_api_response('api/goods/{}'.format(all_goods[0]), get,
                                              data={'short_with_description': True})['data']['goods']
                text = choice(["По вашему запросу найдено:",
                               "Мне удалось найти:",
                               "В нашем каталоге нашлось:",
                               "Вот результат моего поиска:",
                               "Скорее всего, это:"])
                text += "\n{} – {}руб".format(goods['name'], goods['price'])
                res['response']['text'] = text
                res['response']['card'] = {
                    "type": "BigImage",
                    "image_id": "1540737/805f7f3757eea4d5f263",
                    "title": text,
                    "description": goods['short_description'],
                    "button": {
                        "text": "Узнать подробнее на сайте",
                        "url": SHOP_URL + goods['full_link'].strip('/')
                    }
                }
                session['state'] = 'search_goods_showed'
                session['state_value'] = all_goods  # все найденные товары
            else:
                raise ApiError
        except Exception as e:
            make_unknown_error(e, res, session)
        return

    # завершение работы с пользователем
    if find_command(answer, special='end_session'):
        sessionStorage.pop(user_id)
        res['response']['text'] = choice(['До встречи!',
                                          'Всех благ!',
                                          'Надеюсь "увидеть" Вас снова!',
                                          'Всего хорошего!'])
        res['response']['end_session'] = True
        return

    res['response']['text'] = choice(['Извинете, не расслышал. О чём это вы?',
                                      'Не расслышал, что?',
                                      'Не знаю, о чём Вы...',
                                      'Я вас не понимать.'])
    res['response']['buttons'] = get_suggests(user_id)


# получение кнопок-подсказок
def get_suggests(user_id):
    try:
        def get_base_variants():
            if session['authorization']:
                return [["Что бы купить?", "Посоветуй что-нибудь.", "Что интересного?", "Что нового?"],
                        ["Что в моей корзине?", "Какие у меня есть заказы?", "Сделай заказ.", "Выйди из ЛК"],
                        ["Что ты умеешь?"],
                        ["Пока.", "До свидания.", "До встречи.", "Заверши работу."]]
            return [["Что бы купить?", "Посоветуй что-нибудь.", "Что интересного?", "Что нового?"],
                    ["Хочу войти в ЛК.", "Войди в ЛК.", "Хочу авторизироваться.", "Авторизируй меня."],
                    ["Что ты умеешь?"],
                    ["Пока.", "До свидания.", "До встречи.", "Заверши работу."]]
        if user_id in sessionStorage:
            session = sessionStorage[user_id]
            state = session['state']

            if not state or state == WAITING:
                return make_suggests(choose_suggests(get_base_variants()))

            if state in ['goods_showed', 'search_goods_showed']:
                return make_suggests(['Добавь в корзину.', 'Покажи ещё.'])

            if state == 'basket_showed':
                if session['state_value'] == 'empty':
                    return make_suggests(choose_suggests(get_base_variants()))
                return make_suggests(choose_suggests([['Сделай заказ.', 'Создай заказ.', 'Оформи заказ'],
                                                      ['Покажи что-нибудь новое.', 'Покажи что-нибудь.']]))

            if state in ['order_confirm', 'authorization_question',
                         'removing_goods_from_basket']:
                return make_suggests(['Да.', 'Нет.'])

            if state == 'adding_to_basket':
                return make_suggests(['1', str(randint(2, 10)), 'Отмена.'])

            if state in ['authorization_waiting']:
                return make_suggests(['Отмена.'])
    except Exception as e:
        logging.error('Error: {}'.format(e))
    return []


# создать посказки из массива списков подсказок
def make_suggests(lst):
    return [{'title': suggest, 'hide': True} for suggest in lst]


# выбрать подсказки
def choose_suggests(lst):
    for suggests in lst:
        yield choice(suggests)


# найти переданные команды в тексте
def find_command(text, *commands, special=None):
    max_diff = 10
    if special:
        if special == 'what_can':
            commands = [
                "что ты умеешь", "на что ты способен", "для чего ты нужен",
                "ты кто"
            ]
        elif special == 'authorization_wish':
            commands = [
                "войти в лк", "войди в лк", "войти в личный кабинет",
                "войди в личный кабинет", "авторизироваться", "авторизируй меня",
                "давай войдем в личный кабине"
            ]
        elif special == 'de_authorization_wish':
            commands = [
                "деавторизируй меня", "выйди из личного кабинета", "выйди из лк",
                "выйти из личного кабинета", "выйти из лк",
            ]
        elif special == 'interesting_goods':
            commands = [
                "что бы купить", "посоветуй что-нибудь", "что посоветуешь",
                "что интересного", "что нового", "посоветуй интересный товар",
                "покажи интересный товар", "посоветуй хороший товар", "что-нибудь новое",
                "покажи что-нибудь", "чего бы купить"
            ]
        elif special == 'basket_ask':
            commands = [
                "что у меня в корзине", "что в корзине", "что в моей корзине",
                "моя корзина", "корзина", "покажи корзину"
            ]
        elif special == 'make_order_ask':
            commands = [
                "сделай заказ", "сделать заказ", "закажи товары из корзины",
                "создай заказ", "оформи заказ"
            ]
        elif special == 'add_to_basket':
            commands = [
                "в корзину", "давай", "беру",
                "возьму", "добавь к заказу"
            ]
        elif special == 'orders_ask':
            commands = [
                "какие у меня заказы", "мои заказы", "покажи заказы",
                "у меня есть заказы"
            ]
        elif special == 'remove_goods_from_basket_ask':
            commands = [
                "удали из корзины", "убери с корзины", "убери из корзины"
                "удали из корзины товар", "убери с корзины товар", "убери из корзины товар",
                "удали товар", "удали товар номер", "удали товар с артикулом"
            ]
            max_diff = 20
        elif special == 'end_session':
            commands = [
                "пока", "до свидания", "до встречи",
                "заверши работу", "завершить работу", "до скорого"
            ]
        elif special == 'search_goods':
            if ' не' not in ' ' + text:
                if "найди" in text[:20]:
                    # возвращаем текст запроса, начаниющийся после найденного слова
                    return text[text.index("найди") + 5:].strip()
                elif "найти" in text:
                    return text[text.index("найти") + 5:].strip()
                elif "поищи" in text[:20]:
                    return text[text.index("поищи") + 5:].strip()
                elif "поискать" in text:
                    return text[text.index("поискать") + 8:].strip()
            return False

    text_set = {word.strip('.?,!"') for word in text.split()}

    for command in commands:
        if len(text) - len(command) > max_diff:
            continue
        no_in_text = ' не' in ' ' + text
        no_in_command = ' не' in ' ' + text
        if no_in_text and not no_in_command:
            continue
        command_set = set(command.split())
        # если все слова из команды есть в переданном тексте
        if command_set & text_set == command_set:
            return command  # команда найдена
    return False


# проверка на отмену текущего деййствия
def check_cancel(text):
    if find_command(text, "нет", "позже", "потом", "не хочу", "отмена",
                    "отменить", "стоп", "нет, потом", "передумал"):
        return True
    return False


# проверка на согласие
def check_agree(text):
    if find_command(text, "да", "хорошо", "ок", "давай"):
        return True
    return False


# подготовить ответ при неизвестной ошибке
def make_unknown_error(error, res, session):
    logging.error('Error: {}'.format(error))
    messages = ["Что-то пошло не так, давайте попробуем в следующий раз. "
                "Что-нибудь ещё?",
                "Кажется, что-то пошло не по плану. Попробуем в следющий раз. "
                "Что-нибудь ещё желаете?",
                "Что-то мешает мне сделать это, лучше попробуем в следующий раз. "
                "Может, что-нибудь ещё?"]
    message = choice(messages)
    res['response']['text'] = message[0]
    session['state'] = WAITING


# проверка авторизиции пользователя и, в противном случае, подготовка ответа
def check_authorization(session, res):
    if not session['authorization']:
        messages = ["Вы не имеете такой возможности, так как не авторизированы. Хотите войти в личный кибинет?",
                    "Вы не имеете тактой возможности. Желаете авторизоваться?",
                    "Вам недоступна такая функция, необходима авторизация. Хотите войти в личный кабинет?"]
        res['response']['text'] = choice(messages)
        session['state'] = 'authorization_question'
        return False
    return True


# предложение о помощи
def can_help():
    messages = ["Чем ещё могу быть полезен?",
                "Что-нибудь ещё?",
                "что-нибудь ещё желаете?",
                "Могу Вам чем-то ещё помочь?",
                "Вам нужно чем-то ещё помочь?"]
    return choice(messages)


# получение списка элементов корзины в строковых видах
def get_goods_list(basket):
    goods_list = []
    for g in basket:
        goods_list.append('<{id}> {name}, {price}руб * {count}шт = {total}руб'.format(
            id=g['id'],
            name=g['name'][:25].strip('.,') + ('...' if len(g['name']) > 25 else ''),
            count=g['count'],
            price=g['price'],
            total=int(g['count']) * int(g['price'])
        ))
    return goods_list


# получение ответа от API магазина
def get_shop_api_response(url, method, data=dict()):
    return method(SHOP_URL + url.strip('/'), params=data).json()


if __name__ == '__main__':
    # http://vlad26120.pythonanywhere.com/alice_main
    app.run()
