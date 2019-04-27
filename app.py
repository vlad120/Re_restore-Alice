from flask import Flask, request
from requests import get, post, put, delete
from random import choice
import logging
import json


class ApiError(Exception):
    pass


# logging.basicConfig(filename='my_logs.log', level=logging.INFO,
#                     format='%(asctime)s %(levelname)s %(message)s')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

app = Flask(__name__)

sessionStorage = {}

unknown_error = "Что-то пошло не так, давайте попробуем в следующий раз. Что-нибудь ещё?"


@app.route('/alice_main', methods=['POST'])
def main():
    response = {
        'session': request.json['session'],
        'version': request.json['version'],
        'response': {
            'end_session': False
        }
    }

    handle_dialog(request.json, response)
    response['response']['buttons'] = get_suggests(response['session']['user_id'])

    logging.info('Response: %r', request.json)

    return json.dumps(response)


# Функция ведения диалога
def handle_dialog(req, res):
    user_id = req['session']['user_id']

    # новая сессия
    if req['session']['new'] or user_id not in sessionStorage:
        sessionStorage[user_id] = {
            'suggests': [
                "Что бы купить?",
                "Я что-нибудь заказывал у вас?",
                "Что ты умеешь?",
            ],
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
    if (state == 'waiting' and find_command(answer, "нет", "позже", "потом", "не хочу")) or \
            (not state and find_command(answer, "пока", "до свидания", "уйди", "отстань")):
        res['response']['text'] = 'Если что, обращайтесь!'
        res['response']['end_session'] = True
        return

    # если сейчас ведется какой-либо режим
    if state:
        # авторизация
        if state == 'authorization_waiting':
            try:
                data = answer.split()
                # проверка формата
                if len(data) == 2:
                    data = {'login': data[0],
                            'password': data[1]}
                    api_resp = get_shop_api_response('authorizationAPI', get, data)
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
                        res['response']['text'] = 'Ладно, что-нибудь ещё?'
                        session['state'] = 'waiting'
                        return
                    res['response']['text'] = 'Неверный формат! Я же сказал, ' \
                                              'нужно ввести логин и пароль через пробел. Давайте заново.'
            except Exception as e:
                logging.error('Error: {}'.format(request.json, e))
                res['response']['text'] = unknown_error
                session['state'] = 'waiting'
            return

        # запрос на добавление в корзину
        if state == "goods_showed":
            if find_command(answer, "добавь в корзину", "добавить в корзину",
                                    "в корзину", "давай", "беру", "возьму"):
                res['response']['text'] = 'Сколько хотите добавить?'
                session['state'] = 'adding_to_basket'
                return

        # при добавлении товара в корзину
        if state == "adding_to_basket":
            if check_cancel(answer):
                res['response']['text'] = 'Ну, хорошо. Что желаете?'
                return
            for entity in req['request']['nlu']['entities']:
                if entity['type'] == 'YANDEX.NUMBER':
                    try:
                        data = session['authorization']
                        data['count'] = entity['value']
                        api_resp = get_shop_api_response('basketAPI/{}'.format(session['state_value']),
                                                         post, data=data)
                        # при успешном добавлении
                        if api_resp['success']:
                            res['response']['text'] = 'Добавил. Что-нибудь ещё?'
                            session['state'] = 'waiting'
                            return
                        # товара недостаточно
                        elif api_resp['message'] == "Count is more then original":
                            res['response']['text'] = 'Губу, пожалуйста, закатайте обратно. ' \
                                                      'Нет у нас столько в наличии. ' \
                                                      'Максимум – {}шт'.format(api_resp['count'])
                            return
                        # товар уже в корзине
                        elif api_resp['message'] == "Goods has already added":
                            res['response']['text'] = 'Вообще-то, этот товар уже у Вас в корзине.'
                            session['state'] = None  # выходим из режима
                            return
                    except Exception as e:
                        logging.error('Error: {}'.format(request.json, e))
                        res['response']['text'] = unknown_error
                        session['state'] = 'waiting'
                        return
            # если в ответе нет числа
            res['response']['text'] = 'Не понял, сколько?'
            return

        # подтверждение заказа
        if state == 'order_confirm':
            # при согласии
            if check_agree(answer):
                try:
                    api_resp = get_shop_api_response('ordersAPI/curr', post,
                                                     data=session['authorization'])
                    # при успешном размещении заказа
                    if api_resp['success']:
                        res['response']['text'] = "Заказ №{} был создан. В течение некоторого времени " \
                                                  "с Вами свяжется наш менеджер для подтверждения. " \
                                                  "Что-нибудь ещё желаете?".format(api_resp['order_id'])
                        session['state'] = 'waiting'
                    raise ApiError
                except Exception as e:
                    logging.error('Error: {}'.format(request.json, e))
                    res['response']['text'] = unknown_error
                    session['state'] = 'waiting'
            # при отрицании
            elif check_cancel(answer):
                res['response']['text'] = "Хорошо, пока отложим заказ на потом. Чем ещё могу быть полезен?"
                session['state'] = 'waiting'
            # непонятная фраза
            else:
                res['response']['text'] = "Говорите, пожалуйста, понятнее."
            return

    # желание авторизироваться
    if find_command(answer, "хочу войти в лк", "войди в лк",
                            "хочу войти в личный кабинет", "войди в личный кабинет",
                            "хочу авторизироваться", "авторизируй меня"):
        if session['authorization']:
            res['response']['text'] = 'Вы уже авторизированы как "{}"'.format(
                session['authorization']['login']
            )
            return
        res['response']['text'] = 'Ок, для входа в личный кабинет введите с ' \
                                  'клавиатуры свой логин и пароль через пробел.'
        session['state'] = 'authorization_waiting'
        return

    # просмотр 1 интересного (случайного) товара
    if find_command(answer, "что бы купить", "посоветуй что-нибудь", "что посоветуешь"
                            "что интересного", "что нового", "посоветуй интересный товар",
                            "покажи интересный товар", "посоветуй хороший товар"):
        try:
            api_resp = get_shop_api_response('goodsAPI/random', get)
            # при успешном получении товара с сервера
            if api_resp['success']:
                goods = api_resp['goods'][0]  # один товар
                text = 'Могу предложить Вам {} – всего за {}руб.'.format(
                    goods['name'], goods['price']
                )
                res['response']['card'] = {
                    "type": "BigImage",
                    "image_id": "1540737/805f7f3757eea4d5f263",
                    "title": text,
                    "button": {
                        "text": "Узнать подробнее на сайте",
                        "url": SHOP_URL + goods['full_link'].strip('/'),
                        "payload": {}
                    }
                }
                session['state'] = "goods_showed"
                session['state_value'] = goods['id']
                return
            raise ApiError
        except Exception as e:
            logging.error('Error: {}'.format(request.json, e))
            res['response']['text'] = unknown_error
            session['state'] = 'waiting'
            return

    # просмотр корзины
    if find_command(answer, "что у меня в корзине", "что в корзине",
                            "что в моей корзине", "моя корзина", "корзина", "покажи корзину"):
        try:
            api_resp = get_shop_api_response('basketAPI/curr', get,
                                             data=session['authorization'])
            # при успешном получении козины с сервера
            if api_resp['success']:
                basket = api_resp['basket']
                if basket['goods']:
                    text = 'Ваша корзина:\n{}'.format("\n".join(get_goods_list(basket)))
                    text += "\n\nИтого: {} рублей".format(basket['total'])
                else:
                    text = 'Ваша корзина пуста.'
                res['response']['text'] = text
                return
            raise ApiError
        except Exception as e:
            logging.error('Error: {}'.format(request.json, e))
            res['response']['text'] = unknown_error
            session['state'] = 'waiting'
            return

    # запрос на создание заказа
    if find_command(answer, "сделай заказ", "сделать заказ", "закажи товары из корзины", "создай заказ"):
        try:
            api_resp = get_shop_api_response('basketAPI/curr', get,
                                             data=session['authorization'])
            # при успешном получении козины с сервера
            if api_resp['success']:
                basket = api_resp['basket']
                if basket['goods']:
                    text = 'Вы хотите заказать:\n{}'.format("\n".join(get_goods_list(basket)))
                    text += "\n\nНа общую сумму: {} рублей.\nВсё верно?".format(basket['total'])
                    session['state'] = 'order_confirm'
                else:
                    text = 'Ваша корзина пуста. Для создания заказа сначала добавьте туда товары.'
                res['response']['text'] = text
                return
            raise ApiError
        except Exception as e:
            logging.error('Error: {}'.format(request.json, e))
            res['response']['text'] = unknown_error
            session['state'] = 'waiting'
            return

    res['response']['text'] = 'Извинете, не расслышал. О чём это вы?'
    res['response']['buttons'] = get_suggests(user_id)


# получение кнопок-подсказок
def get_suggests(user_id):
    try:
        session = sessionStorage[user_id]
        state = session['state']
        if not state or state == 'waiting':
            if session['authorization']:
                variants = [["Что бы купить?", "Посоветуй что-нибудь.", "Что интересного?", "Что нового?"],
                            ["Что в моей корзине?", "Какие у меня есть заказы?", "Сделай заказ.", "Выйди из ЛК"],
                            ["Что ты умеешь?"]]
            else:
                variants = [["Что бы купить?", "Посоветуй что-нибудь.", "Что интересного?", "Что нового?"],
                            ["Хочу войти в ЛК.", "Войди в ЛК.", "Хочу авторизироваться.", "Авторизируй меня."],
                            ["Что ты умеешь?"]]
            return make_suggests(choose_suggests(variants))
        if state == 'order_confirm':
            return make_suggests(['Да', 'Нет'])
        if state == 'adding_to_basket':
            return make_suggests(['1', '2', 'Отмена'])
        if state in ['authorization_waiting']:
            return make_suggests(['Отмена'])
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
def find_command(text, *commands):
    for command in commands:
        len_diff = len(text) - len(command)
        if len_diff > 10:
            continue
        no_in_text = ' не' in ' ' + text
        no_in_command = ' не' in ' ' + text
        if no_in_text and not no_in_command:
            continue
        text_set = set(word.strip('.?,!"') for word in text.split())
        command = set(command.split())
        if command & text_set == command:
            return True
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


# получение списка элементов корзины в строковых видах
def get_goods_list(basket):
    for g in basket['goods']:
        yield '- {name} * {count}шт ({price}руб * {count}) = {total}руб'.format(
                name=g['name'], count=g['count'], price=g['price'],
                total=int(g['count']) * int(g['price'])
        )


# получение ответа от API магазина
def get_shop_api_response(url, method, data=dict()):
    return method(SHOP_URL + url.strip('/'), params=data).json()


# SHOP_URL = "https://re_restore.ru/"
SHOP_URL = "http://127.0.0.1:8080/"


if __name__ == '__main__':
    app.run()
