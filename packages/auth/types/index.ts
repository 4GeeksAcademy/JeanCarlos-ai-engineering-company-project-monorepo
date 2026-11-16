export interface Profile {
  id: string;
  user_id: string;
  name: string | null;
  phone: string | null;
  address: string | null;
}

export interface Account {
  id: string;
  username: string;
  email: string | null;
  role: "admin" | "manager" | "user";
  profile: Profile | null;
}
