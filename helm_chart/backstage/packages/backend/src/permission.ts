import {
  coreServices,
  createBackendModule,
} from '@backstage/backend-plugin-api';
import {
  AuthorizeResult,
  isPermission,
  PolicyDecision,
} from '@backstage/plugin-permission-common';
import {
  PermissionPolicy,
  PolicyQuery,
  PolicyQueryUser,
} from '@backstage/plugin-permission-node';
import { policyExtensionPoint } from '@backstage/plugin-permission-node/alpha';
import {
  actionExecutePermission,
  taskReadPermission,
  taskCancelPermission,
} from '@backstage/plugin-scaffolder-common/alpha';
import {
  createScaffolderActionConditionalDecision,
  scaffolderActionConditions,
  createScaffolderTaskConditionalDecision,
  scaffolderTaskConditions,
} from '@backstage/plugin-scaffolder-backend/alpha';

// Explicit allowlists keep new plugin permissions denied until reviewed.
const readPermissions = new Set([
  'catalog.entity.read',
  'catalog.location.read',
  'scaffolder.template.parameter.read',
  'scaffolder.template.step.read',
]);
const operatorPermissions = new Set([
  'scaffolder.task.create',
  'scaffolder.template.dry-run',
]);

export class PlatformPolicy implements PermissionPolicy {
  constructor(
    private readonly operatorGroups: string[],
    private readonly repoUrl = '',
  ) {}

  async handle(
    request: PolicyQuery,
    user?: PolicyQueryUser,
  ): Promise<PolicyDecision> {
    if (!user) return { result: AuthorizeResult.DENY };
    const operator = user.info.ownershipEntityRefs.some(ref =>
      this.operatorGroups.includes(ref),
    );

    if (
      isPermission(request.permission, taskReadPermission) ||
      isPermission(request.permission, taskCancelPermission)
    ) {
      return createScaffolderTaskConditionalDecision(
        request.permission,
        scaffolderTaskConditions.isTaskOwner({
          createdBy: [user.info.userEntityRef],
        }),
      );
    }
    if (isPermission(request.permission, actionExecutePermission)) {
      if (!operator) return { result: AuthorizeResult.DENY };
      return createScaffolderActionConditionalDecision(request.permission, {
        anyOf: [
          scaffolderActionConditions.hasActionId({
            actionId: 'fetch:template',
          }),
          {
            allOf: [
              scaffolderActionConditions.hasActionId({
                actionId: 'publish:github:pull-request',
              }),
              scaffolderActionConditions.hasStringProperty({
                key: 'repoUrl',
                value: this.repoUrl,
              }),
            ],
          },
        ],
      });
    }
    if (
      readPermissions.has(request.permission.name) ||
      (operator && operatorPermissions.has(request.permission.name))
    ) {
      return { result: AuthorizeResult.ALLOW };
    }
    return { result: AuthorizeResult.DENY };
  }
}

export const permissionModule = createBackendModule({
  pluginId: 'permission',
  moduleId: 'platform-policy',
  register(reg) {
    reg.registerInit({
      deps: { policy: policyExtensionPoint, config: coreServices.rootConfig },
      async init({ policy, config }) {
        policy.setPolicy(
          new PlatformPolicy(
            config.getStringArray('platform.operatorGroups'),
            config.getOptionalString('platform.gitopsRepoUrl') ?? '',
          ),
        );
      },
    });
  },
});
