import {
  coreServices,
  createBackendModule,
} from '@backstage/backend-plugin-api';
import { scaffolderTemplatingExtensionPoint } from '@backstage/plugin-scaffolder-node/alpha';

export const templatesModule = createBackendModule({
  pluginId: 'scaffolder',
  moduleId: 'platform-templates',
  register(reg) {
    reg.registerInit({
      deps: {
        templating: scaffolderTemplatingExtensionPoint,
        config: coreServices.rootConfig,
      },
      async init({ templating, config }) {
        // Operators supply a company-approved repository in the mounted config;
        // users cannot redirect the publishing credential to an arbitrary repo.
        templating.addTemplateGlobals({
          gitopsRepoUrl:
            config.getOptionalString('platform.gitopsRepoUrl') ?? '',
        });
      },
    });
  },
});
