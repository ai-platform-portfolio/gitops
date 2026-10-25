import {
  createPermission,
  AuthorizeResult,
} from '@backstage/plugin-permission-common';
import {
  taskReadPermission,
  actionExecutePermission,
} from '@backstage/plugin-scaffolder-common/alpha';
import { PlatformPolicy } from './permission';

const user = (groups: string[] = []) => ({
  token: 'unused',
  credentials: {
    $$type: '@backstage/BackstageCredentials' as const,
    principal: { type: 'user' as const, userEntityRef: 'user:default/alice' },
    expiresAt: undefined,
  },
  info: {
    userEntityRef: 'user:default/alice',
    ownershipEntityRefs: ['user:default/alice', ...groups],
  },
});
const query = (name: string) => ({
  permission: createPermission({ name, attributes: {} }),
});
const policy = new PlatformPolicy(
  ['group:default/platform-team'],
  [
    'git.company.internal?owner=platform&repo=services',
    'github.com?owner=ai-platform-portfolio&repo=cluster-deployments',
  ],
);

describe('platform permissions', () => {
  it('denies anonymous callers and unknown permissions even to operators', async () => {
    expect(await policy.handle(query('catalog.entity.read'))).toEqual({
      result: AuthorizeResult.DENY,
    });
    expect(
      await policy.handle(
        query('new-plugin.destroy'),
        user(['group:default/platform-team']),
      ),
    ).toEqual({ result: AuthorizeResult.DENY });
  });

  it('allows readers to discover software but denies catalog and template mutation', async () => {
    expect(await policy.handle(query('catalog.entity.read'), user())).toEqual({
      result: AuthorizeResult.ALLOW,
    });
    for (const name of [
      'catalog.location.create',
      'catalog.entity.delete',
      'scaffolder.task.create',
    ]) {
      expect(await policy.handle(query(name), user())).toEqual({
        result: AuthorizeResult.DENY,
      });
    }
  });

  it('requires trusted group membership for task creation', async () => {
    expect(
      await policy.handle(
        query('scaffolder.task.create'),
        user(['group:default/platform-team']),
      ),
    ).toEqual({ result: AuthorizeResult.ALLOW });
  });

  it('limits task access to the caller, including operators', async () => {
    const result = await policy.handle(
      { permission: taskReadPermission },
      user(['group:default/platform-team']),
    );
    expect(result).toMatchObject({
      result: AuthorizeResult.CONDITIONAL,
      conditions: {
        rule: 'IS_TASK_OWNER',
        params: { createdBy: ['user:default/alice'] },
      },
    });
  });

  it('limits operator actions to generation and reviewed pull requests', async () => {
    expect(
      await policy.handle({ permission: actionExecutePermission }, user()),
    ).toEqual({ result: AuthorizeResult.DENY });
    const result = await policy.handle(
      { permission: actionExecutePermission },
      user(['group:default/platform-team']),
    );
    expect(result).toMatchObject({
      result: AuthorizeResult.CONDITIONAL,
      conditions: {
        anyOf: [
          { params: { actionId: 'fetch:template' } },
          {
            allOf: [
              { params: { actionId: 'publish:github:pull-request' } },
              {
                anyOf: [
                  {
                    params: {
                      key: 'repoUrl',
                      value:
                        'git.company.internal?owner=platform&repo=services',
                    },
                  },
                  {
                    params: {
                      key: 'repoUrl',
                      value:
                        'github.com?owner=ai-platform-portfolio&repo=cluster-deployments',
                    },
                  },
                ],
              },
            ],
          },
        ],
      },
    });
  });
});
