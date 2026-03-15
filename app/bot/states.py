from aiogram.fsm.state import State, StatesGroup


class AdminStates(StatesGroup):
    create_group = State()
    rename_group = State()

    survey_create_title = State()
    survey_add_question_type = State()
    survey_add_question_text = State()
    survey_add_question_options = State()


class UserStates(StatesGroup):
    survey_text = State()