import io
import json
from pathlib import Path

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from robot_op.extract import sections, pdf
from robot_op.robot import doc_view
from robot_op.models import Document, JsonVersion, Program
from robot_op.web.app import create_app
from conftest import demo_settings

FIXTURES=Path(__file__).parent / "fixtures"


def test_real_practice_steps_are_nested_without_losing_their_text():
    c=json.loads((FIXTURES/'extraction-225.json').read_text())
    d=sections.extract(c['type'],c['text'],c['tables'])
    assert [len(s['steps']) for s in d['stages']]==[3,6,2]
    assert d['stages'][0]['steps'][0].startswith('Оформление документов')
    assert 'руководителя практикой от предприятия)' in d['stages'][1]['steps'][-1]
    assert all(s['summary']=='' for s in d['stages'])


def test_generic_stage_lists_preserve_decimals_versions_and_intro():
    d={'stages':[{'title':'Подготовка','summary':'Задания: 1. Установить Python 3.11. 2. Рассчитать коэффициент 1.5.'},
                 {'title':'Работа','summary':'• Собрать данные • Проверить данные'},
                 {'title':'Отчёт','summary':'Используется Python 3.11 и коэффициент 1.5.'}]}
    items=next(b['items'] for b in doc_view.build('practice',d)['blocks'] if b['title']=='Этапы практики')
    assert items[0]['summary']=='Задания:'
    assert items[0]['steps']==['Установить Python 3.11.','Рассчитать коэффициент 1.5.']
    assert items[1]['steps']==['Собрать данные','Проверить данные']
    assert items[2]['summary']=='Используется Python 3.11 и коэффициент 1.5.'
    assert not items[2].get('steps')


def test_ml_methodological_links_survive_with_labels_and_origins():
    c=json.loads((FIXTURES/'extraction-239.json').read_text())
    d=sections.extract(c['type'],c['text'],c['tables'])
    resources=d['resources']
    assert all(not r['title'].isdigit() for r in resources)
    stepik=next(r for r in resources if r.get('url')=='https://stepik.org/course/4852/syllabus')
    assert 'Введение в Data Science' in stepik['title']
    assert stepik['origins']==[{'section':'5.1.2 Методические указания для обучающихся по самостоятельной работе','page':6}]
    colab=next(r for r in resources if r.get('url','').startswith('https://colab.research.google.com/'))
    assert 'Colaboratory' in colab['title'] and '#scrollTo=GJBs_flRovLc' in colab['url']
    assert colab['origins'][0]['page']==6 and colab['origins'][0]['section'].startswith('5.1.1 ')
    assert any(r.get('url')=='http://ai.lector.ru/?go=python' for r in resources)
    missing=next(r for r in resources if 'Воронцов' in r['title'])
    assert not missing.get('url')
    assert all('colab' not in t['title'].lower() for t in d['topics'])


def test_resources_are_generic_deduplicated_and_keep_wrapped_query_urls():
    text="""5.1 Методические указания
1. Документация другого курса
https://example.org/long-
path/book.html?a=1&b=2#part
2. Та же ссылка https://example.org/long-path/book.html?a=1&b=2#part

9 Ресурсы сети Интернет
1. Еще источник https://example.org/long-path/book.html?a=1&b=2#part
"""
    d=sections.extract('rpd',text)
    matching=[r for r in d['resources'] if r.get('url')=='https://example.org/long-path/book.html?a=1&b=2#part']
    assert len(matching)==1 and {o['page'] for o in matching[0]['origins']}=={1,2}


def test_pdf_hidden_uri_link_is_preserved_without_unsafe_schemes():
    from reportlab.pdfgen import canvas
    from demo_site.render import _register_fonts
    _register_fonts()
    stream=io.BytesIO();c=canvas.Canvas(stream)
    c.setFont('DejaVu',11)
    c.drawString(40,790,'5.1 Методические указания')
    c.drawString(40,760,'1. Учебный курс по данным — ссылка')
    c.linkURL('https://courses.example/course?id=3#intro',(40,757,370,774),relative=0)
    c.linkURL('javascript:alert(1)',(40,720,100,735),relative=0)
    c.save();data=stream.getvalue()
    links=pdf.resource_links(data)
    result=sections.extract('rpd','\f\n'.join(pdf.page_texts(data)),hyperlinks=links)
    resource=next(r for r in result['resources'] if r.get('url')=='https://courses.example/course?id=3#intro')
    assert 'Учебный курс по данным' in resource['title']
    assert resource['origins'][0]['page']==1
    assert not any(r.get('url','').startswith('javascript:') for r in result['resources'])
    assert len([r for r in result['resources'] if 'Учебный курс по данным' in r['title']])==1


def test_app_renders_nested_steps_and_resources_in_literature(tmp_path):
    app=create_app(demo_settings(tmp_path),start_worker=False)
    with app.state.db.Session() as ses:
        p=Program(id='another-program',level='master',level_title='Магистратура',year=2027,code='01.04.01',profile='Другая программа',form='Очная')
        ses.add(p);ses.add(Document(id=1,program_id=p.id,doc_type='practice',title='Практика',url='https://example.org/source.pdf'));ses.flush()
        ses.add(JsonVersion(document_id=1,kind='final',version=1,data={'document':{'pages':2},'data':{
            'stages':[{'title':'Подготовка','summary':'1. Собрать данные. 2. Обработать данные.'}],
            'literature':['Автор. Учебник. 2025. https://books.example/book'],
            'resources':[{'title':'Учебник','url':'https://books.example/book','origins':[{'section':'7 Основная литература','page':2}]},
                         {'title':'Видеокурс','url':'https://courses.example/course?a=1&b=2','origins':[{'section':'5.1 Методические указания','page':1}]}]}}))
        ses.commit()
    with TestClient(app) as client:
        soup=BeautifulSoup(client.get('/op/another-program/doc/1').text,'html.parser')
        assert [li.get_text(strip=True) for li in soup.select('.app-topics > li > ol > li')]==['Собрать данные.','Обработать данные.']
        heading=soup.find('h2',string='Литература')
        lit=heading.parent
        assert lit.select_one('a[href="https://courses.example/course?a=1&b=2"]')
        assert '5.1 Методические указания' in lit.get_text() and 'стр. 1' in lit.get_text()
        assert len(lit.select('a[href="https://books.example/book"]'))==1
        assert '7 Основная литература' in lit.get_text() and 'стр. 2' in lit.get_text()


def test_consecutive_urls_remain_separate_and_malformed_uri_does_not_crash():
    from robot_op.extract.resources import collect, safe_url
    text='9 Ресурсы сети Интернет\nhttps://first.example/a\nhttps://second.example/b'
    assert {r['url'] for r in collect(text)}=={'https://first.example/a','https://second.example/b'}
    assert safe_url('https://[broken')==''


def test_resource_entry_is_not_a_heading_and_query_wrap_is_preserved():
    from robot_op.extract.resources import collect
    assert collect('1. Ресурсы по курсу https://example.org/course')[0]['url']=='https://example.org/course'
    assert collect('https://example.org/path?id=1&\nother=2')[0]['url']=='https://example.org/path?id=1&other=2'


def test_long_uri_caption_merges_missing_address_and_correct_section():
    from robot_op.extract.resources import collect
    text='5.1 Методические указания\n6 Учебно-методическое обеспечение самостоятельной работы\n1. Длинное название\nучебного курса\nпо анализу данных\nс дополнительными материалами\nдля самостоятельного\nповторения и практического\nизучения — ссылка'
    result=collect(text,[{'url':'https://example.org/course','page':1,'text':'ссылка','context':text}])
    assert len(result)==1 and result[0]['url']=='https://example.org/course'
    assert result[0]['title']=='Длинное название учебного курса по анализу данных с дополнительными материалами для самостоятельного повторения и практического изучения'
    assert result[0]['origins'][0]['section']=='6 Учебно-методическое обеспечение самостоятельной работы'


def test_similar_bibliography_url_does_not_hide_a_distinct_resource():
    d={'literature':['Книга https://example.org/a/b'], 'resources':[
        {'url':'https://example.org/a','title':'Другой адрес','origins':[{'section':'5 Методические указания','page':1}]}]}
    block=next(b for b in doc_view.build('rpd',d)['blocks'] if b['title']=='Литература')
    assert block['resources'][0]['url']=='https://example.org/a'


def test_wrapped_book_url_stays_complete_and_retains_origin():
    d={'literature':['Книга https://books.example/long- path/book.pdf'], 'resources':[
        {'url':'https://books.example/long-path/book.pdf','title':'Книга','origins':[{'section':'7 Литература','page':2}]}]}
    block=next(b for b in doc_view.build('rpd',d)['blocks'] if b['title']=='Литература')
    assert block['items']==['Книга https://books.example/long-path/book.pdf']
    assert block['book_origins']==[[{'section':'7 Литература','page':2}]]
    assert not block['resources']
