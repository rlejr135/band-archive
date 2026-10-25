import { createContext, useContext } from 'react';

export const WorkspaceContext = createContext(null);

export const useWorkspace = () => {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error('useWorkspace must be used in BandWorkspace');
  return value;
};
