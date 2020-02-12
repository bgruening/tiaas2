from django.db import connections
import codecs
from Crypto.Cipher import Blowfish
from django.conf import settings

cipher = Blowfish.new(settings.GALAXY_SECRET)


TRAINING_QUEUE_HEADERS = [
    "state",
    "job_runner_external_id",
    "tool_id",
    "user_id",
    "create_time",
]
TRAINING_QUEUE_QUERY = """
SELECT
        job.state,
        job.job_runner_external_id AS extid,
        regexp_replace(job.tool_id, '.*toolshed.*/repos/', ''),
        substring(md5(COALESCE(galaxy_user.username, 'Anonymous') || now()::date), 0, 7),
        date_trunc('second', job.create_time) AS created
FROM
        job, galaxy_user
WHERE
        job.user_id = galaxy_user.id
        AND job.create_time > (now() AT TIME ZONE 'UTC' - '%s hours'::interval)
        AND galaxy_user.id
                IN (
                                SELECT
                                        galaxy_user.id
                                FROM
                                        galaxy_user, user_group_association, galaxy_group
                                WHERE
                                        galaxy_group.name = 'training-%s'
                                        AND galaxy_group.id = user_group_association.group_id
                                        AND user_group_association.user_id = galaxy_user.id
                        )
ORDER BY
        job.create_time DESC
LIMIT 300
"""

TRAINING_USERS_QUERY = """
SELECT
        substring(md5(COALESCE(galaxy_user.username, 'Anonymous') || now()::date), 0, 7)
FROM
        galaxy_user
WHERE
        galaxy_user.id
                IN (
                                SELECT
                                        galaxy_user.id
                                FROM
                                        galaxy_user, user_group_association, galaxy_group
                                WHERE
                                        galaxy_group.name = 'training-%s'
                                        AND galaxy_group.id = user_group_association.group_id
                                        AND user_group_association.user_id = galaxy_user.id
                        )
"""


# Create your views here.


def get_roles():
    roles = fetch_all(
        "select id, name from role where type in ('admin', 'system') and name like 'training-%%'"
    )
    for role in roles:
        yield {"id": role[0], "name": role[1]}


def create_role(training_id):
    execute(
        "insert into role (name, description, type, create_time, update_time, deleted) values ('%s', 'Autogenerated role', 'system', now(), now(), false)"
        % training_id
    )
    # get the role back
    role = fetch_all("select id from role where name = '%s'" % training_id)
    for r in role:
        return r[0]
    return -1


def get_jobs(training_id, hours):
    jobs = fetch_all(TRAINING_QUEUE_QUERY % (hours, training_id))
    for job in jobs:
        yield dict(zip(TRAINING_QUEUE_HEADERS, job))


def get_users(training_id):
    users = fetch_all(TRAINING_USERS_QUERY % training_id)
    for user in users:
        yield user[0]


def get_groups():
    groups = fetch_all(
        "select id, name from galaxy_group where name like 'training-%%'"
    )
    for group in groups:
        yield {"id": group[0], "name": group[1]}


def create_group(training_id, role_id):
    execute(
        "insert into galaxy_group (name, create_time, update_time, deleted) values ('%s', now(), now(), false)"
        % training_id
    )
    # get the role back
    groups = fetch_all("select id from galaxy_group where name = '%s'" % training_id)
    group_id = -1
    for group in groups:
        group_id = group[0]
    execute(
        "insert into group_role_association (group_id, role_id, create_time, update_time) values (%s, %s, now(), now())"
        % (group_id, role_id)
    )
    return group_id


def add_group_user(group_id, user_id):
    execute(
        "insert into user_group_association (user_id, group_id, create_time, update_time) values (%s, %s, now(), now())"
        % (user_id, group_id)
    )


def execute(query):
    with connections["galaxy"].cursor() as cursor:
        cursor.execute(query)


def fetch_all(query):
    with connections["galaxy"].cursor() as cursor:
        cursor.execute(query)
        result = cursor.fetchall()
    return result


def authenticate(request):
    auth_token = request.COOKIES.get("galaxysession", None)
    if not auth_token:
        return None

    galaxy_encoded_session_id = codecs.decode(auth_token, "hex")
    galaxy_session_id = (
        cipher.decrypt(galaxy_encoded_session_id).decode("utf-8").lstrip("!")
    )

    with connections["galaxy"].cursor() as cursor:
        cursor.execute(
            """
            SELECT user_id, username
            FROM galaxy_session
            JOIN galaxy_user ON galaxy_session.user_id = galaxy_user.id
            WHERE session_key = '%s';
            """
            % galaxy_session_id
        )
        user = cursor.fetchone()

    if not user:
        return None

    user_id = user[0]

    return user_id
