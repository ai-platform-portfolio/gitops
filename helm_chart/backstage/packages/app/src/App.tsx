import { createApp } from '@backstage/frontend-defaults';
import signalsPlugin from '@backstage/plugin-signals/alpha';
import catalogPlugin from '@backstage/plugin-catalog/alpha';
import { SignInPageBlueprint } from '@backstage/plugin-app-react';
import { SignInPage } from '@backstage/core-components';
import { createFrontendModule } from '@backstage/frontend-plugin-api';
import { microsoftAuthApiRef } from '@backstage/core-plugin-api';
import { navModule } from './modules/nav';

const signIn = SignInPageBlueprint.make({
  params: {
    loader: async () => props =>
      (
        <SignInPage
          {...props}
          provider={{
            id: 'microsoft',
            title: 'Microsoft Entra ID',
            message: 'Sign in with your Microsoft account',
            apiRef: microsoftAuthApiRef,
          }}
        />
      ),
  },
});

export const appFeatures = [catalogPlugin, signalsPlugin, navModule];

export default createApp({
  features: [
    ...appFeatures,
    createFrontendModule({
      pluginId: 'app',
      extensions: [signIn],
    }),
  ],
});
