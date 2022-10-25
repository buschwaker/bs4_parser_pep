import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import requests_cache
from tqdm import tqdm

from configs import configure_argument_parser, configure_logging
from constants import (
    BASE_DIR, EXPECTED_STATUS, EXPECTED_TYPES, MAIN_DOC_URL, PEPS
)
from outputs import control_output
from utils import get_response, find_tag


def pep(session):
    results = [('Статус', 'Количество')]
    response = session.get(PEPS)
    soup = BeautifulSoup(response.text, features='lxml')
    pep_section = find_tag(soup, 'section', attrs={'id': 'numerical-index'})
    rows = pep_section.find_all('tr')[1:]
    SEEN_STATUSES = {
        'Active': 0,
        'Accepted': 0,
        'Deferred': 0,
        'Final': 0,
        'Provisional': 0,
        'Rejected': 0,
        'Superseded': 0,
        'Withdrawn': 0,
        'Draft': 0,
    }
    TOTAL_PEPS = 0
    for row in tqdm(rows):
        elements_in_row = row.find_all('td')
        type_status, num_pep, url = (
            elements_in_row[0].text,
            elements_in_row[1].text,
            elements_in_row[1].find('a')['href']
        )
        pep_type, pep_status = (
            (type_status[0], type_status[1])
            if len(type_status) == 2 else (type_status, '')
        )
        single_pep_url = urljoin(PEPS, url)
        session = requests_cache.CachedSession()
        response = session.get(single_pep_url)
        soup = BeautifulSoup(response.text, features='lxml')
        single_pep_section = find_tag(
            soup, 'dl', attrs={'class': 'rfc2822 field-list simple'}
        )
        dt_dd = single_pep_section.children
        status_tag = None
        type_tag = None

        for tag in dt_dd:
            if re.match('.*Status.*$', str(tag)):
                status_tag = tag.find_next_sibling()
            elif re.match('.*Type.*$', str(tag)):
                type_tag = tag.find_next_sibling()

        if pep_type != type_tag.text[0]:
            logging.error(
                f"Для PEP{num_pep} статус в карточке: "
                f"{type_tag.text}. Ожидалось: "
                f"{EXPECTED_TYPES[pep_type]}")
        if status_tag.text not in EXPECTED_STATUS[pep_status]:
            logging.error(
                f"Для PEP{num_pep} статус в карточке: "
                f"{status_tag.text}. Ожидалось: "
                f"{', '.join(EXPECTED_STATUS[pep_status])}")
        if SEEN_STATUSES.get(status_tag.text) is not None:
            SEEN_STATUSES[status_tag.text] += 1
        else:
            SEEN_STATUSES.update({status_tag.text: 1})
        TOTAL_PEPS += 1
    for status, number in SEEN_STATUSES.items():
        results.append((status, number))
    assert sum(SEEN_STATUSES.values()) == TOTAL_PEPS
    results.append(('Total', TOTAL_PEPS))
    return results


def whats_new(session):
    whats_new_url = urljoin(MAIN_DOC_URL, 'whatsnew/')
    response = get_response(session, whats_new_url)
    soup = BeautifulSoup(response.text, features='lxml')
    main_div = find_tag(soup, 'section', attrs={'id': 'what-s-new-in-python'})
    div_with_ul = find_tag(main_div, 'div', attrs={'class': 'toctree-wrapper'})
    sections_by_python = div_with_ul.find_all(
        'li', attrs={'class': 'toctree-l1'}
    )
    results = [('Ссылка на статью', 'Заголовок', 'Редактор, Автор'), ]
    for section in tqdm(sections_by_python):
        version_a_tag = find_tag(section, 'a')
        href = version_a_tag['href']
        version_link = urljoin(whats_new_url, href)
        session = requests_cache.CachedSession()
        response = get_response(session, version_link)
        response.encoding = 'utf8'
        soup = BeautifulSoup(response.text, features='lxml')
        h1 = find_tag(soup, 'h1')
        dl = find_tag(soup, 'dl')
        results.append(
            (version_link, h1.text, dl.text.replace('\n', ' ')
             )
        )
    return results


def latest_versions(session):
    response = get_response(session, MAIN_DOC_URL)
    response.encoding = 'utf-8'
    soup = BeautifulSoup(response.text, features='lxml')
    sidebar = find_tag(soup, 'div', attrs={'class': 'sphinxsidebarwrapper'})
    ul_tags = sidebar.find_all('ul')
    for ul_tag in ul_tags:
        if 'All versions' in ul_tag.text:
            a_tags = ul_tag.find_all('a')
            break
    else:
        raise Exception('Ничего не нашлось')
    results = [('Ссылка на документацию', 'Версия', 'Статус')]
    pattern = r'Python (?P<version>\d\.\d+) \((?P<status>.*)\)'
    for a_tag in a_tags:
        link = a_tag['href']
        text_match = re.search(pattern, a_tag.text)
        if text_match is not None:
            version, status = text_match.groups()
        else:
            version, status = a_tag.text, ''
        results.append(
            (link, version, status)
        )

    return results


def download(session):
    downloads_url = urljoin(MAIN_DOC_URL, 'download.html')
    response = get_response(session, downloads_url)
    response.encoding = 'utf-8'
    soup = BeautifulSoup(response.text, features='lxml')
    main_tag = find_tag(soup, 'div', attrs={'role': 'main'})
    table_tag = find_tag(main_tag, 'table', attrs={'class': 'docutils'})
    pdf_a4_tag = find_tag(
        table_tag, 'a', {'href': re.compile(r'.+pdf-a4\.zip$')}
    )
    archive_url = urljoin(downloads_url, pdf_a4_tag['href'])
    filename = archive_url.split('/')[-1]
    downloads_dir = BASE_DIR / 'downloads'
    downloads_dir.mkdir(exist_ok=True)
    archive_path = downloads_dir / filename
    response = session.get(archive_url)
    with open(archive_path, 'wb') as file:
        file.write(response.content)
    logging.info(f'Архив был загружен и сохранён: {archive_path}')


MODE_TO_FUNCTION = {
    'whats-new': whats_new,
    'latest-versions': latest_versions,
    'download': download,
    'pep': pep
}


def main():
    configure_logging()
    logging.info('Парсер запущен!')

    arg_parser = configure_argument_parser(MODE_TO_FUNCTION.keys())
    args = arg_parser.parse_args()
    logging.info(f'Аргументы командной строки: {args}')

    session = requests_cache.CachedSession()
    if args.clear_cache:
        session.cache.clear()

    parser_mode = args.mode
    results = MODE_TO_FUNCTION[parser_mode](session)

    if results is not None:
        control_output(results, args)
    logging.info('Парсер завершил работу.')


if __name__ == '__main__':
    main()
