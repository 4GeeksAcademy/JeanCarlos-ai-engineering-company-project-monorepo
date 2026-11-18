export interface Profile {
  id: string;
  user_id: string;
  name: string | null;
  phone: string | null;
  address: string | null;
}

/** GET /auth/me (AUTH-01): identity is the email; there is no username. */
export interface Account {
  id: string;
  email: string;
  role: "admin" | "manager" | "user";
  profile: Profile | null;
}
