import { createApp } from '@backstage/frontend-defaults';
import catalogPlugin from '@backstage/plugin-catalog/alpha';
import { createFrontendModule } from '@backstage/frontend-plugin-api';
import { SignInPageBlueprint } from '@backstage/plugin-app-react';
import { SignInPage } from '@backstage/core-components';
import {
  OpenIdConnectApi,
  ProfileInfoApi,
  BackstageIdentityApi,
  SessionApi,
} from '@backstage/core-plugin-api';
import { OAuth2 } from '@backstage/core-app-api';
import {
  createApiRef,
  ApiBlueprint,
  configApiRef,
  discoveryApiRef,
  oauthRequestApiRef,
} from '@backstage/frontend-plugin-api';
import { navModule } from './modules/nav';

const oidcAuthApiRef = createApiRef<
  OpenIdConnectApi & ProfileInfoApi & BackstageIdentityApi & SessionApi
>().with({ id: 'auth.company' });
const oidcApi = ApiBlueprint.make({
  name: 'company-oidc',
  params: defineParams =>
    defineParams({
      api: oidcAuthApiRef,
      deps: {
        configApi: configApiRef,
        discoveryApi: discoveryApiRef,
        oauthRequestApi: oauthRequestApiRef,
      },
      factory: ({ configApi, discoveryApi, oauthRequestApi }) =>
        OAuth2.create({
          configApi,
          discoveryApi,
          oauthRequestApi,
          environment: configApi.getOptionalString('auth.environment'),
          provider: { id: 'oidc', title: 'Company identity', icon: () => null },
          defaultScopes: ['openid', 'profile', 'email'],
        }),
    }),
});

const signIn = SignInPageBlueprint.make({
  params: {
    loader: async () => props =>
      (
        <SignInPage
          {...props}
          provider={{
            id: 'company-oidc',
            title: 'Company identity',
            message: 'Sign in with your company account',
            apiRef: oidcAuthApiRef,
          }}
        />
      ),
  },
});

export default createApp({
  features: [
    catalogPlugin,
    navModule,
    createFrontendModule({
      pluginId: 'app',
      extensions: [oidcApi, signIn],
    }),
  ],
});
