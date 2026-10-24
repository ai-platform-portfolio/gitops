import { screen } from '@testing-library/react';
import { renderTestApp } from '@backstage/frontend-test-utils';
import {
  PageBlueprint,
  storageApiRef,
  useApi,
} from '@backstage/frontend-plugin-api';
import userSettingsModule from '@backstage/plugin-app-module-user-settings';
import { appFeatures } from './App';

function StorageProbe() {
  const storage = useApi(storageApiRef);
  return (
    <div>{storage.forBucket('catalog-filters') && 'Storage is available'}</div>
  );
}

describe('App', () => {
  it('resolves the production user-settings storage dependencies', async () => {
    renderTestApp({
      features: [...appFeatures, userSettingsModule],
      extensions: [
        PageBlueprint.make({
          name: 'storage-probe',
          params: {
            path: '/storage-probe',
            loader: async () => <StorageProbe />,
          },
        }),
      ],
      initialRouteEntries: ['/storage-probe'],
    });

    expect(await screen.findByText('Storage is available')).toBeInTheDocument();
  });
});
