import argparse
import datetime
from github import Github
import json
import os
import subprocess

EMPTY_CHANGELOG = """# CodeQL Action and CodeQL Runner Changelog

## [UNRELEASED]

No user facing changes.

"""

# Name of the remote
ORIGIN = 'origin'

# Runs git with the given args and returns the stdout.
# Raises an error if git does not exit successfully.
def run_git(*args):
  cmd = ['git', *args]
  p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
  if (p.returncode != 0):
    raise Exception('Call to ' + ' '.join(cmd) + ' exited with code ' + str(p.returncode) + ' stderr:' + p.stderr.decode('ascii'))
  return p.stdout.decode('ascii')

# Returns true if the given branch exists on the origin remote
def branch_exists_on_remote(branch_name):
  return run_git('ls-remote', '--heads', ORIGIN, branch_name).strip() != ''

# Opens a PR from the given branch to the release branch
def open_pr(repo, all_commits, short_main_sha, new_branch_name, source_branch, target_branch, conductor, include_mergeback_in_changelog):
  # Sort the commits into the pull requests that introduced them,
  # and any commits that don't have a pull request
  pull_requests = []
  commits_without_pull_requests = []
  for commit in all_commits:
    pr = get_pr_for_commit(repo, commit)

    if pr is None:
      commits_without_pull_requests.append(commit)
    elif not any(p for p in pull_requests if p.number == pr.number):
      pull_requests.append(pr)

  print('Found ' + str(len(pull_requests)) + ' pull requests')
  print('Found ' + str(len(commits_without_pull_requests)) + ' commits not in a pull request')

  # Sort PRs and commits by age
  pull_requests = sorted(pull_requests, key=lambda pr: pr.number)
  commits_without_pull_requests = sorted(commits_without_pull_requests, key=lambda c: c.commit.author.date)

  # Start constructing the body text
  body = []
  body.append('Merging ' + short_main_sha + ' into ' + target_branch)

  body.append('')
  body.append('Conductor for this PR is @' + conductor)

  # List all PRs merged
  if len(pull_requests) > 0:
    body.append('')
    body.append('Contains the following pull requests:')
    for pr in pull_requests:
      merger = get_merger_of_pr(repo, pr)
      body.append('- #' + str(pr.number) + ' - ' + pr.title +' (@' + merger + ')')

  # List all commits not part of a PR
  if len(commits_without_pull_requests) > 0:
    body.append('')
    body.append('Contains the following commits not from a pull request:')
    for commit in commits_without_pull_requests:
      author_description = ' (@' + commit.author.login + ')' if commit.author is not None else ''
      body.append('- ' + commit.sha + ' - ' + get_truncated_commit_message(commit) + author_description)

  body.append('')
  body.append('Please review the following:')
  body.append(' - [ ] The CHANGELOG displays the correct version and date.')
  body.append(' - [ ] The CHANGELOG includes all relevant, user-facing changes since the last release.')
  body.append(' - [ ] There are no unexpected commits being merged into the ' + target_branch + ' branch.')
  body.append(' - [ ] The docs team is aware of any documentation changes that need to be released.')
  if include_mergeback_in_changelog:
    body.append(' - [ ] The mergeback PR is merged back into ' + source_branch + ' after this PR is merged.')

  title = 'Merge ' + source_branch + ' into ' + target_branch

  # Create the pull request
  # PR checks won't be triggered on PRs created by Actions. Therefore mark the PR as draft so that
  # a maintainer can take the PR out of draft, thereby triggering the PR checks.
  pr = repo.create_pull(title=title, body='\n'.join(body), head=new_branch_name, base=target_branch, draft=True)
  print('Created PR #' + str(pr.number))

  # Assign the conductor
  pr.add_to_assignees(conductor)
  print('Assigned PR to ' + conductor)

# Gets a list of the SHAs of all commits that have happened on main
# since the release branched off.
# This will not include any commits that exist on the release branch
# that aren't on main.
def get_commit_difference(repo, source_branch, target_branch):
  # Passing split nothing means that the empty string splits to nothing: compare `''.split() == []`
  # to `''.split('\n') == ['']`.
  commits = run_git('log', '--pretty=format:%H', ORIGIN + '/' + target_branch + '..' + ORIGIN + '/' + source_branch).strip().split()

  # Convert to full-fledged commit objects
  commits = [repo.get_commit(c) for c in commits]

  # Filter out merge commits for PRs
  return list(filter(lambda c: not is_pr_merge_commit(c), commits))

# Is the given commit the automatic merge commit from when merging a PR
def is_pr_merge_commit(commit):
  return commit.committer is not None and commit.committer.login == 'web-flow' and len(commit.parents) > 1

# Gets a copy of the commit message that should display nicely
def get_truncated_commit_message(commit):
  message = commit.commit.message.split('\n')[0]
  if len(message) > 60:
    return message[:57] + '...'
  else:
    return message

# Converts a commit into the PR that introduced it to the main branch.
# Returns the PR object, or None if no PR could be found.
def get_pr_for_commit(repo, commit):
  prs = commit.get_pulls()

  if prs.totalCount > 0:
    # In the case that there are multiple PRs, return the earliest one
    prs = list(prs)
    sorted_prs = sorted(prs, key=lambda pr: int(pr.number))
    return sorted_prs[0]
  else:
    return None

# Get the person who merged the pull request.
# For most cases this will be the same as the author, but for PRs opened
# by external contributors getting the merger will get us the GitHub
# employee who reviewed and merged the PR.
def get_merger_of_pr(repo, pr):
  return repo.get_commit(pr.merge_commit_sha).author.login

def get_current_version():
  with open('package.json', 'r') as f:
    return json.load(f)['version']

def get_today_string():
  today = datetime.datetime.today()
  return '{:%d %b %Y}'.format(today)

def update_changelog(version):
  if (os.path.exists('CHANGELOG.md')):
    content = ''
    with open('CHANGELOG.md', 'r') as f:
      content = f.read()
  else:
    content = EMPTY_CHANGELOG

  newContent = content.replace('[UNRELEASED]', version + ' - ' + get_today_string(), 1)

  with open('CHANGELOG.md', 'w') as f:
    f.write(newContent)


def main():
  parser = argparse.ArgumentParser('update-release-branch.py')

  parser.add_argument(
    '--github-token',
    type=str,
    required=True,
    help='GitHub token, typically from GitHub Actions.'
  )
  parser.add_argument(
    '--repository-nwo',
    type=str,
    required=True,
    help='The nwo of the repository, for example github/codeql-action.'
  )
  parser.add_argument(
    '--source-branch',
    type=str,
    required=True,
    help='The branch being merged from, typically "main" for a v2 release or "v2" for a v1 release.'
  )
  parser.add_argument(
    '--target-branch',
    type=str,
    required=True,
    help='The branch being merged into, typically "v2" for a v2 release or "v1" for a v1 release.'
  )
  parser.add_argument(
    '--conductor',
    type=str,
    required=True,
    help='The GitHub handle of the person who is conducting the release process.'
  )
  parser.add_argument(
    '--perform-v2-to-v1-backport',
    action='store_true',
    help='Pass this flag if this release is a backport from v2 to v1.'
  )

  args = parser.parse_args()

  repo = Github(args.github_token).get_repo(args.repository_nwo)
  version = get_current_version()

  if args.perform_v2_to_v1_backport:
    # Change the version number to a v1 equivalent
    version = get_current_version()
    version = f'1{version[1:]}'

  # Print what we intend to go
  print('Considering difference between ' + args.source_branch + ' and ' + args.target_branch)
  short_main_sha = run_git('rev-parse', '--short', ORIGIN + '/' + args.source_branch).strip()
  print('Current head of ' + args.source_branch + ' is ' + short_main_sha)

  # See if there are any commits to merge in
  commits = get_commit_difference(repo=repo, source_branch=args.source_branch, target_branch=args.target_branch)
  if len(commits) == 0:
    print('No commits to merge from ' + args.source_branch + ' to ' + args.target_branch)
    return

  # The branch name is based off of the name of branch being merged into
  # and the SHA of the branch being merged from. Thus if the branch already
  # exists we can assume we don't need to recreate it.
  new_branch_name = 'update-v' + version + '-' + short_main_sha
  print('Branch name is ' + new_branch_name)

  # Check if the branch already exists. If so we can abort as this script
  # has already run on this combination of branches.
  if branch_exists_on_remote(new_branch_name):
    print('Branch ' + new_branch_name + ' already exists. Nothing to do.')
    return

  # Create the new branch and push it to the remote
  print('Creating branch ' + new_branch_name)
  run_git('checkout', '-b', new_branch_name, ORIGIN + '/' + args.source_branch)

  if args.perform_v2_to_v1_backport:
    # Migrate the package version number from a v2 version number to a v1 version number
    print(f'Setting version number to {version}')
    subprocess.run(['npm', 'version', version, '--no-git-tag-version'])
    run_git('reset', 'HEAD~1')
    run_git('add', 'package.json', 'package-lock.json')

    # Migrate the changelog notes from v2 version numbers to v1 version numbers
    print('Migrating changelog notes from v2 to v1')
    subprocess.run(['sed', '-i', 's/## 2./## 1./g', 'CHANGELOG.md'])

    # Amend the commit generated by `npm version` to update the CHANGELOG
    run_git('add', 'CHANGELOG.md')
    run_git('commit', '--amend', '-m', f'Update version and changelog for v{version}')
  else:
    # We don't need to do this for a v1 release, since the changelog has already been updated in the v2 branch.
    print('Updating changelog')
    update_changelog(version)

    # Create a commit that updates the CHANGELOG
    run_git('add', 'CHANGELOG.md')
    run_git('commit', '-m', f'Update changelog for v{version}')

  run_git('push', ORIGIN, new_branch_name)

  # Open a PR to update the branch
  open_pr(
    repo,
    commits,
    short_main_sha,
    new_branch_name,
    source_branch=args.source_branch,
    target_branch=args.target_branch,
    conductor=args.conductor,
    include_mergeback_in_changelog=not args.perform_v2_to_v1_backport
  )

if __name__ == '__main__':
  main()
