"""
Generates the neofetch-style SVG banner on yohannzapata's GitHub profile.

Adapted from `today.py` by Andrew Grant (Andrew6rant), 2022-2025
Original: https://github.com/Andrew6rant/Andrew6rant

Requires the environment variables ACCESS_TOKEN and USER_NAME.
ACCESS_TOKEN needs a fine-grained PAT with All Repositories access:
  Account permissions:    read:Followers, read:Starring, read:Watching
  Repository permissions: read:Commit statuses, read:Contents, read:Metadata
"""

import datetime
import hashlib
import os
import time

import requests
from dateutil import relativedelta
from lxml import etree

HEADERS = {'authorization': 'token ' + os.environ['ACCESS_TOKEN']}
USER_NAME = os.environ['USER_NAME']
CACHE_DIR = 'cache'
COMMENT_SIZE = 7
QUERY_COUNT = {'user_getter': 0, 'follower_getter': 0, 'graph_repos_stars': 0,
               'recursive_loc': 0, 'graph_commits': 0, 'loc_query': 0}


def cache_filename():
    return os.path.join(CACHE_DIR, hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest() + '.txt')


BIRTHDAY = datetime.datetime(2006, 6, 4)


def daily_readme(birthday):
    """Returns elapsed time as 'XX years, XX months, XX days'."""
    diff = relativedelta.relativedelta(datetime.datetime.today(), birthday)
    return '{} {}, {} {}, {} {}{}'.format(
        diff.years, 'year' + format_plural(diff.years),
        diff.months, 'month' + format_plural(diff.months),
        diff.days, 'day' + format_plural(diff.days),
        ' \U0001F382' if (diff.months == 0 and diff.days == 0) else '')


def format_plural(unit):
    return 's' if unit != 1 else ''


def simple_request(func_name, query, variables):
    request = requests.post('https://api.github.com/graphql',
                            json={'query': query, 'variables': variables}, headers=HEADERS)
    if request.status_code == 200:
        return request
    raise Exception(func_name, ' has failed with a', request.status_code, request.text, QUERY_COUNT)


def graph_repos_stars(count_type, owner_affiliation, cursor=None):
    """Returns total repository or star count."""
    query_count('graph_repos_stars')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges { node { ... on Repository { nameWithOwner stargazers { totalCount } } } }
                pageInfo { endCursor hasNextPage }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(graph_repos_stars.__name__, query, variables)
    if count_type == 'repos':
        return request.json()['data']['user']['repositories']['totalCount']
    elif count_type == 'stars':
        return stars_counter(request.json()['data']['user']['repositories']['edges'])


def recursive_loc(owner, repo_name, data, cache_comment,
                  addition_total=0, deletion_total=0, my_commits=0, cursor=None):
    """Pages through a repository's commit history 100 commits at a time."""
    query_count('recursive_loc')
    query = '''
    query ($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef { target { ... on Commit {
                history(first: 100, after: $cursor) {
                    totalCount
                    edges { node { ... on Commit { committedDate }
                            author { user { id } } deletions additions } }
                    pageInfo { endCursor hasNextPage }
                }
            } } }
        }
    }'''
    variables = {'repo_name': repo_name, 'owner': owner, 'cursor': cursor}
    request = requests.post('https://api.github.com/graphql',
                            json={'query': query, 'variables': variables}, headers=HEADERS)
    if request.status_code == 200:
        if request.json()['data']['repository']['defaultBranchRef'] is not None:
            return loc_counter_one_repo(
                owner, repo_name, data, cache_comment,
                request.json()['data']['repository']['defaultBranchRef']['target']['history'],
                addition_total, deletion_total, my_commits)
        return 0
    force_close_file(data, cache_comment)
    if request.status_code == 403:
        raise Exception('Too many requests in a short amount of time!\n'
                        'You\'ve hit the non-documented anti-abuse limit!')
    raise Exception('recursive_loc() has failed with a', request.status_code, request.text, QUERY_COUNT)


def loc_counter_one_repo(owner, repo_name, data, cache_comment, history,
                         addition_total, deletion_total, my_commits):
    """Only counts the LOC of commits authored by USER_NAME."""
    for node in history['edges']:
        if node['node']['author']['user'] == OWNER_ID:
            my_commits += 1
            addition_total += node['node']['additions']
            deletion_total += node['node']['deletions']

    if history['edges'] == [] or not history['pageInfo']['hasNextPage']:
        return addition_total, deletion_total, my_commits
    return recursive_loc(owner, repo_name, data, cache_comment,
                         addition_total, deletion_total, my_commits, history['pageInfo']['endCursor'])


def loc_query(owner_affiliation, comment_size=0, force_cache=False, cursor=None, edges=None):
    """Queries 60 repos at a time; larger queries 502, smaller ones rate-limit."""
    edges = [] if edges is None else edges
    query_count('loc_query')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
                edges { node { ... on Repository { nameWithOwner
                    defaultBranchRef { target { ... on Commit { history { totalCount } } } } } } }
                pageInfo { endCursor hasNextPage }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(loc_query.__name__, query, variables)
    repos = request.json()['data']['user']['repositories']
    if repos['pageInfo']['hasNextPage']:
        edges += repos['edges']
        return loc_query(owner_affiliation, comment_size, force_cache,
                         repos['pageInfo']['endCursor'], edges)
    return cache_builder(edges + repos['edges'], comment_size, force_cache)


def cache_builder(edges, comment_size, force_cache, loc_add=0, loc_del=0):
    """Only re-counts LOC for repositories whose commit count changed since last run."""
    cached = True
    os.makedirs(CACHE_DIR, exist_ok=True)
    filename = cache_filename()
    try:
        with open(filename, 'r') as f:
            data = f.readlines()
    except FileNotFoundError:
        data = ['This line is a comment block. Write whatever you want here.\n'] * comment_size
        with open(filename, 'w') as f:
            f.writelines(data)

    if len(data) - comment_size != len(edges) or force_cache:
        cached = False
        flush_cache(edges, filename, comment_size)
        with open(filename, 'r') as f:
            data = f.readlines()

    cache_comment = data[:comment_size]
    data = data[comment_size:]
    for index in range(len(edges)):
        repo_hash, commit_count, *__ = data[index].split()
        if repo_hash == hashlib.sha256(edges[index]['node']['nameWithOwner'].encode('utf-8')).hexdigest():
            try:
                if int(commit_count) != edges[index]['node']['defaultBranchRef']['target']['history']['totalCount']:
                    owner, repo_name = edges[index]['node']['nameWithOwner'].split('/')
                    loc = recursive_loc(owner, repo_name, data, cache_comment)
                    data[index] = '{} {} {} {} {}\n'.format(
                        repo_hash,
                        edges[index]['node']['defaultBranchRef']['target']['history']['totalCount'],
                        loc[2], loc[0], loc[1])
            except TypeError:  # empty repository
                data[index] = repo_hash + ' 0 0 0 0\n'
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    for line in data:
        loc = line.split()
        loc_add += int(loc[3])
        loc_del += int(loc[4])
    return [loc_add, loc_del, loc_add - loc_del, cached]


def flush_cache(edges, filename, comment_size):
    """Wipes the cache when the repository count changes or the file is first created."""
    with open(filename, 'r') as f:
        data = f.readlines()[:comment_size] if comment_size > 0 else []
    with open(filename, 'w') as f:
        f.writelines(data)
        for node in edges:
            f.write(hashlib.sha256(node['node']['nameWithOwner'].encode('utf-8')).hexdigest() + ' 0 0 0 0\n')


def force_close_file(data, cache_comment):
    """Preserves partial cache data when the program is about to crash."""
    filename = cache_filename()
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    print('There was an error while writing to the cache file. The file,', filename,
          'has had the partial data saved and closed.')


def stars_counter(data):
    total_stars = 0
    for node in data:
        total_stars += node['node']['stargazers']['totalCount']
    return total_stars


def svg_overwrite(filename, age_data, commit_data, star_data, repo_data,
                  contrib_data, follower_data, loc_data):
    """Parses the SVG and replaces the placeholder elements with live stats."""
    tree = etree.parse(filename)
    root = tree.getroot()
    justify_format(root, 'age_data', age_data)
    justify_format(root, 'commit_data', commit_data)
    justify_format(root, 'star_data', star_data)
    justify_format(root, 'repo_data', repo_data, 5)
    justify_format(root, 'contrib_data', contrib_data)
    justify_format(root, 'follower_data', follower_data)
    justify_format(root, 'loc_data', loc_data[2])
    justify_format(root, 'loc_add', loc_data[0])
    justify_format(root, 'loc_del', loc_data[1])
    tree.write(filename, encoding='utf-8', xml_declaration=True)


def justify_format(root, element_id, new_text, length=0):
    """Sets the element text and pads the sibling `_dots` element to keep columns aligned."""
    if isinstance(new_text, int):
        new_text = '{:,}'.format(new_text)
    new_text = str(new_text)
    find_and_replace(root, element_id, new_text)
    just_len = max(0, length - len(new_text))
    if just_len <= 2:
        dot_string = {0: '', 1: ' ', 2: '. '}[just_len]
    else:
        dot_string = ' ' + ('.' * just_len) + ' '
    find_and_replace(root, element_id + '_dots', dot_string)


def find_and_replace(root, element_id, new_text):
    element = root.find(".//*[@id='{}']".format(element_id))
    if element is not None:
        element.text = new_text


def commit_counter(comment_size):
    """Sums my commit count from the cache file built by cache_builder."""
    total_commits = 0
    with open(cache_filename(), 'r') as f:
        data = f.readlines()[comment_size:]
    for line in data:
        total_commits += int(line.split()[2])
    return total_commits


def user_getter(username):
    """Returns the account ID and creation time of the user."""
    query_count('user_getter')
    query = '''
    query($login: String!){ user(login: $login) { id createdAt } }'''
    request = simple_request(user_getter.__name__, query, {'login': username})
    return {'id': request.json()['data']['user']['id']}, request.json()['data']['user']['createdAt']


def follower_getter(username):
    query_count('follower_getter')
    query = '''
    query($login: String!){ user(login: $login) { followers { totalCount } } }'''
    request = simple_request(follower_getter.__name__, query, {'login': username})
    return int(request.json()['data']['user']['followers']['totalCount'])


def query_count(funct_id):
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1


def perf_counter(funct, *args):
    start = time.perf_counter()
    funct_return = funct(*args)
    return funct_return, time.perf_counter() - start


def formatter(query_type, difference):
    print('{:<23}'.format('   ' + query_type + ':'), sep='', end='')
    if difference > 1:
        print('{:>12}'.format('%.4f' % difference + ' s '))
    else:
        print('{:>12}'.format('%.4f' % (difference * 1000) + ' ms'))


if __name__ == '__main__':
    print('Calculation times:')
    user_data, user_time = perf_counter(user_getter, USER_NAME)
    OWNER_ID, acc_date = user_data
    formatter('account data', user_time)

    age_data, age_time = perf_counter(daily_readme, BIRTHDAY)
    formatter('age calculation', age_time)

    total_loc, loc_time = perf_counter(
        loc_query, ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'], COMMENT_SIZE)
    formatter('LOC (cached)' if total_loc[-1] else 'LOC (no cache)', loc_time)

    commit_data, commit_time = perf_counter(commit_counter, COMMENT_SIZE)
    formatter('commit counter', commit_time)
    star_data, star_time = perf_counter(graph_repos_stars, 'stars', ['OWNER'])
    formatter('star counter', star_time)
    repo_data, repo_time = perf_counter(graph_repos_stars, 'repos', ['OWNER'])
    formatter('repo counter', repo_time)
    contrib_data, contrib_time = perf_counter(
        graph_repos_stars, 'repos', ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])
    formatter('contrib counter', contrib_time)
    follower_data, follower_time = perf_counter(follower_getter, USER_NAME)
    formatter('follower counter', follower_time)

    for index in range(len(total_loc) - 1):
        total_loc[index] = '{:,}'.format(total_loc[index])

    for svg in ('dark_mode.svg', 'light_mode.svg'):
        svg_overwrite(svg, age_data, commit_data, star_data, repo_data,
                      contrib_data, follower_data, total_loc[:-1])

    print('Total GitHub GraphQL API calls:', '{:>3}'.format(sum(QUERY_COUNT.values())))
    for funct_name, count in QUERY_COUNT.items():
        print('{:<28}'.format('   ' + funct_name + ':'), '{:>6}'.format(count))
