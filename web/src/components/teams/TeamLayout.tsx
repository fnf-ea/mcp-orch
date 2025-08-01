'use client';

import { useEffect, ReactNode } from 'react';
import { useParams, usePathname } from 'next/navigation';
import Link from 'next/link';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { 
  Users, 
  Settings, 
  Activity, 
  BarChart3,
  Home,
  FolderOpen
} from 'lucide-react';
import { useTeamStore, Organization } from '@/stores/teamStore';
import { cn } from '@/lib/utils';

interface TeamLayoutProps {
  children: ReactNode;
}

export function TeamLayout({ children }: TeamLayoutProps) {
  const params = useParams();
  const pathname = usePathname();
  const teamId = params.teamId as string;
  
  const { 
    selectedTeam,
    userTeams,
    loading,
    setSelectedTeam,
    getTeamById
  } = useTeamStore();

  useEffect(() => {
    if (teamId && userTeams.length > 0) {
      const team = getTeamById(teamId);
      if (team && (!selectedTeam || selectedTeam.id !== teamId)) {
        setSelectedTeam(team);
      }
    }
  }, [teamId, userTeams, selectedTeam, setSelectedTeam, getTeamById]);

  const navigationItems = [
    {
      label: 'Overview',
      href: `/teams/${teamId}/overview`,
      icon: BarChart3,
      description: 'Team Overview'
    },
    {
      label: 'Projects',
      href: `/teams/${teamId}/projects`,
      icon: FolderOpen,
      description: 'Team Projects'
    },
    {
      label: 'Members',
      href: `/teams/${teamId}/members`,
      icon: Users,
      description: 'Team Member Management'
    },
    {
      label: 'Activity',
      href: `/teams/${teamId}/activity`,
      icon: Activity,
      description: 'Team Activity'
    },
    {
      label: 'Settings',
      href: `/teams/${teamId}/settings`,
      icon: Settings,
      description: 'Team Settings'
    }
  ];

  const isActive = (href: string) => {
    return pathname === href || pathname.startsWith(href);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-gray-900 mx-auto mb-4"></div>
          <p className="text-muted-foreground">Loading team information...</p>
        </div>
      </div>
    );
  }

  if (!selectedTeam) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <p className="text-muted-foreground">Team not found.</p>
          <Button asChild className="mt-4">
            <Link href="/teams">Back to Team List</Link>
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      {/* Breadcrumbs */}
      <div className="border-b bg-muted/30">
        <div className="container mx-auto px-6 py-2">
          <div className="flex items-center space-x-2 text-sm text-muted-foreground">
            <Link href="/" className="hover:text-foreground flex items-center gap-1">
              <Home className="h-3 w-3" />
              Home
            </Link>
            <span>/</span>
            <Link href="/teams" className="hover:text-foreground">
              Teams
            </Link>
            <span>/</span>
            <span className="text-foreground font-medium">{selectedTeam.name}</span>
          </div>
        </div>
      </div>

      {/* Team Header */}
      <div className="border-b bg-background">
        <div className="container mx-auto px-6 py-6">
          <div className="flex items-start justify-between">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-3 mb-2">
                <h1 className="text-2xl font-bold text-foreground truncate">
                  {selectedTeam.name}
                </h1>
                {selectedTeam.role && (
                  <Badge variant="outline" className={
                    selectedTeam.role === 'OWNER' ? 'border-yellow-300 text-yellow-700 bg-yellow-50' :
                    selectedTeam.role === 'ADMIN' ? 'border-blue-300 text-blue-700 bg-blue-50' :
                    'border-gray-300 text-gray-700 bg-gray-50'
                  }>
                    {selectedTeam.role === 'OWNER' ? 'Owner' : 
                     selectedTeam.role === 'ADMIN' ? 'Admin' : 'Member'}
                  </Badge>
                )}
              </div>
              {selectedTeam.description && (
                <p className="text-muted-foreground text-sm leading-relaxed">
                  {selectedTeam.description}
                </p>
              )}
              <div className="flex items-center gap-4 mt-3 text-sm text-muted-foreground">
                <div className="flex items-center gap-1">
                  <Users className="h-4 w-4" />
                  <span>{selectedTeam.member_count || 0} members</span>
                </div>
                {selectedTeam.created_at && (
                  <div>
                    Created: {new Date(selectedTeam.created_at).toLocaleDateString('en-US')}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Navigation Tabs */}
      <div className="border-b bg-background">
        <div className="container mx-auto px-6">
          <nav className="flex space-x-0">
            {navigationItems.map((item) => {
              const Icon = item.icon;
              const active = isActive(item.href);
              
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    "flex items-center gap-2 px-4 py-3 border-b-2 transition-colors text-sm font-medium",
                    active
                      ? "border-primary text-primary"
                      : "border-transparent text-muted-foreground hover:text-foreground hover:border-muted-foreground/50"
                  )}
                  title={item.description}
                >
                  <Icon className="h-4 w-4" />
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </div>
      </div>

      {/* Page Content */}
      <div className="container mx-auto px-6 py-6">
        {children}
      </div>
    </div>
  );
}