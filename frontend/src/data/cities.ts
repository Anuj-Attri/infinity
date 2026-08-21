// Bundled major world cities → IANA timezone.
// ~200 cities chosen for breadth: capitals, top metros by population,
// and culturally significant places. The OS handles the time math from
// the IANA tz — this table only solves the "city name → tz id" lookup
// that Intl can't do itself.
//
// Search behavior should be case-insensitive substring match against
// either `city` or `country`.

export type CityEntry = {
  city: string;
  country: string;
  tz: string;
};

export const CITIES: CityEntry[] = [
  // North America — USA
  { city: "New York",       country: "USA",          tz: "America/New_York" },
  { city: "Los Angeles",    country: "USA",          tz: "America/Los_Angeles" },
  { city: "Chicago",        country: "USA",          tz: "America/Chicago" },
  { city: "Houston",        country: "USA",          tz: "America/Chicago" },
  { city: "Phoenix",        country: "USA",          tz: "America/Phoenix" },
  { city: "Philadelphia",   country: "USA",          tz: "America/New_York" },
  { city: "San Antonio",    country: "USA",          tz: "America/Chicago" },
  { city: "San Diego",      country: "USA",          tz: "America/Los_Angeles" },
  { city: "Dallas",         country: "USA",          tz: "America/Chicago" },
  { city: "San Jose",       country: "USA",          tz: "America/Los_Angeles" },
  { city: "Austin",         country: "USA",          tz: "America/Chicago" },
  { city: "Jacksonville",   country: "USA",          tz: "America/New_York" },
  { city: "San Francisco",  country: "USA",          tz: "America/Los_Angeles" },
  { city: "Indianapolis",   country: "USA",          tz: "America/Indiana/Indianapolis" },
  { city: "Columbus",       country: "USA",          tz: "America/New_York" },
  { city: "Charlotte",      country: "USA",          tz: "America/New_York" },
  { city: "Seattle",        country: "USA",          tz: "America/Los_Angeles" },
  { city: "Denver",         country: "USA",          tz: "America/Denver" },
  { city: "Boston",         country: "USA",          tz: "America/New_York" },
  { city: "Detroit",        country: "USA",          tz: "America/Detroit" },
  { city: "Nashville",      country: "USA",          tz: "America/Chicago" },
  { city: "Portland",       country: "USA",          tz: "America/Los_Angeles" },
  { city: "Las Vegas",      country: "USA",          tz: "America/Los_Angeles" },
  { city: "Memphis",        country: "USA",          tz: "America/Chicago" },
  { city: "Louisville",     country: "USA",          tz: "America/Kentucky/Louisville" },
  { city: "Baltimore",      country: "USA",          tz: "America/New_York" },
  { city: "Milwaukee",      country: "USA",          tz: "America/Chicago" },
  { city: "Atlanta",        country: "USA",          tz: "America/New_York" },
  { city: "Miami",          country: "USA",          tz: "America/New_York" },
  { city: "Minneapolis",    country: "USA",          tz: "America/Chicago" },
  { city: "New Orleans",    country: "USA",          tz: "America/Chicago" },
  { city: "Washington",     country: "USA",          tz: "America/New_York" },
  { city: "Honolulu",       country: "USA",          tz: "Pacific/Honolulu" },
  { city: "Anchorage",      country: "USA",          tz: "America/Anchorage" },

  // North America — Canada / Mexico
  { city: "Toronto",        country: "Canada",       tz: "America/Toronto" },
  { city: "Montreal",       country: "Canada",       tz: "America/Toronto" },
  { city: "Vancouver",      country: "Canada",       tz: "America/Vancouver" },
  { city: "Calgary",        country: "Canada",       tz: "America/Edmonton" },
  { city: "Ottawa",         country: "Canada",       tz: "America/Toronto" },
  { city: "Edmonton",       country: "Canada",       tz: "America/Edmonton" },
  { city: "Winnipeg",       country: "Canada",       tz: "America/Winnipeg" },
  { city: "Halifax",        country: "Canada",       tz: "America/Halifax" },
  { city: "Mexico City",    country: "Mexico",       tz: "America/Mexico_City" },
  { city: "Guadalajara",    country: "Mexico",       tz: "America/Mexico_City" },
  { city: "Monterrey",      country: "Mexico",       tz: "America/Monterrey" },
  { city: "Tijuana",        country: "Mexico",       tz: "America/Tijuana" },
  { city: "Havana",         country: "Cuba",         tz: "America/Havana" },
  { city: "Panama City",    country: "Panama",       tz: "America/Panama" },
  { city: "San José",       country: "Costa Rica",   tz: "America/Costa_Rica" },

  // South America
  { city: "São Paulo",      country: "Brazil",       tz: "America/Sao_Paulo" },
  { city: "Rio de Janeiro", country: "Brazil",       tz: "America/Sao_Paulo" },
  { city: "Brasília",       country: "Brazil",       tz: "America/Sao_Paulo" },
  { city: "Salvador",       country: "Brazil",       tz: "America/Bahia" },
  { city: "Buenos Aires",   country: "Argentina",    tz: "America/Argentina/Buenos_Aires" },
  { city: "Córdoba",        country: "Argentina",    tz: "America/Argentina/Cordoba" },
  { city: "Santiago",       country: "Chile",        tz: "America/Santiago" },
  { city: "Lima",           country: "Peru",         tz: "America/Lima" },
  { city: "Bogotá",         country: "Colombia",     tz: "America/Bogota" },
  { city: "Medellín",       country: "Colombia",     tz: "America/Bogota" },
  { city: "Caracas",        country: "Venezuela",    tz: "America/Caracas" },
  { city: "Quito",          country: "Ecuador",      tz: "America/Guayaquil" },
  { city: "La Paz",         country: "Bolivia",      tz: "America/La_Paz" },
  { city: "Montevideo",     country: "Uruguay",      tz: "America/Montevideo" },
  { city: "Asunción",       country: "Paraguay",     tz: "America/Asuncion" },

  // Europe — Western
  { city: "London",         country: "UK",           tz: "Europe/London" },
  { city: "Manchester",     country: "UK",           tz: "Europe/London" },
  { city: "Birmingham",     country: "UK",           tz: "Europe/London" },
  { city: "Edinburgh",      country: "UK",           tz: "Europe/London" },
  { city: "Glasgow",        country: "UK",           tz: "Europe/London" },
  { city: "Dublin",         country: "Ireland",      tz: "Europe/Dublin" },
  { city: "Paris",          country: "France",       tz: "Europe/Paris" },
  { city: "Marseille",      country: "France",       tz: "Europe/Paris" },
  { city: "Lyon",           country: "France",       tz: "Europe/Paris" },
  { city: "Nice",           country: "France",       tz: "Europe/Paris" },
  { city: "Berlin",         country: "Germany",      tz: "Europe/Berlin" },
  { city: "Munich",         country: "Germany",      tz: "Europe/Berlin" },
  { city: "Hamburg",        country: "Germany",      tz: "Europe/Berlin" },
  { city: "Frankfurt",      country: "Germany",      tz: "Europe/Berlin" },
  { city: "Cologne",        country: "Germany",      tz: "Europe/Berlin" },
  { city: "Madrid",         country: "Spain",        tz: "Europe/Madrid" },
  { city: "Barcelona",      country: "Spain",        tz: "Europe/Madrid" },
  { city: "Seville",        country: "Spain",        tz: "Europe/Madrid" },
  { city: "Valencia",       country: "Spain",        tz: "Europe/Madrid" },
  { city: "Lisbon",         country: "Portugal",     tz: "Europe/Lisbon" },
  { city: "Porto",          country: "Portugal",     tz: "Europe/Lisbon" },
  { city: "Rome",           country: "Italy",        tz: "Europe/Rome" },
  { city: "Milan",          country: "Italy",        tz: "Europe/Rome" },
  { city: "Naples",         country: "Italy",        tz: "Europe/Rome" },
  { city: "Turin",          country: "Italy",        tz: "Europe/Rome" },
  { city: "Florence",       country: "Italy",        tz: "Europe/Rome" },
  { city: "Venice",         country: "Italy",        tz: "Europe/Rome" },
  { city: "Amsterdam",      country: "Netherlands",  tz: "Europe/Amsterdam" },
  { city: "Rotterdam",      country: "Netherlands",  tz: "Europe/Amsterdam" },
  { city: "Brussels",       country: "Belgium",      tz: "Europe/Brussels" },
  { city: "Antwerp",        country: "Belgium",      tz: "Europe/Brussels" },
  { city: "Vienna",         country: "Austria",      tz: "Europe/Vienna" },
  { city: "Zurich",         country: "Switzerland",  tz: "Europe/Zurich" },
  { city: "Geneva",         country: "Switzerland",  tz: "Europe/Zurich" },
  { city: "Bern",           country: "Switzerland",  tz: "Europe/Zurich" },
  { city: "Luxembourg",     country: "Luxembourg",   tz: "Europe/Luxembourg" },
  { city: "Monaco",         country: "Monaco",       tz: "Europe/Monaco" },

  // Europe — Nordic
  { city: "Stockholm",      country: "Sweden",       tz: "Europe/Stockholm" },
  { city: "Gothenburg",     country: "Sweden",       tz: "Europe/Stockholm" },
  { city: "Copenhagen",     country: "Denmark",      tz: "Europe/Copenhagen" },
  { city: "Oslo",           country: "Norway",       tz: "Europe/Oslo" },
  { city: "Bergen",         country: "Norway",       tz: "Europe/Oslo" },
  { city: "Helsinki",       country: "Finland",      tz: "Europe/Helsinki" },
  { city: "Reykjavík",      country: "Iceland",      tz: "Atlantic/Reykjavik" },

  // Europe — Eastern
  { city: "Warsaw",         country: "Poland",       tz: "Europe/Warsaw" },
  { city: "Kraków",         country: "Poland",       tz: "Europe/Warsaw" },
  { city: "Prague",         country: "Czech Republic", tz: "Europe/Prague" },
  { city: "Budapest",       country: "Hungary",      tz: "Europe/Budapest" },
  { city: "Bucharest",      country: "Romania",      tz: "Europe/Bucharest" },
  { city: "Sofia",          country: "Bulgaria",     tz: "Europe/Sofia" },
  { city: "Athens",         country: "Greece",       tz: "Europe/Athens" },
  { city: "Belgrade",       country: "Serbia",       tz: "Europe/Belgrade" },
  { city: "Zagreb",         country: "Croatia",      tz: "Europe/Zagreb" },
  { city: "Bratislava",     country: "Slovakia",     tz: "Europe/Bratislava" },
  { city: "Ljubljana",      country: "Slovenia",     tz: "Europe/Ljubljana" },
  { city: "Tallinn",        country: "Estonia",      tz: "Europe/Tallinn" },
  { city: "Riga",           country: "Latvia",       tz: "Europe/Riga" },
  { city: "Vilnius",        country: "Lithuania",    tz: "Europe/Vilnius" },
  { city: "Minsk",          country: "Belarus",      tz: "Europe/Minsk" },
  { city: "Kyiv",           country: "Ukraine",      tz: "Europe/Kyiv" },
  { city: "Moscow",         country: "Russia",       tz: "Europe/Moscow" },
  { city: "St Petersburg",  country: "Russia",       tz: "Europe/Moscow" },
  { city: "Novosibirsk",    country: "Russia",       tz: "Asia/Novosibirsk" },
  { city: "Yekaterinburg",  country: "Russia",       tz: "Asia/Yekaterinburg" },
  { city: "Vladivostok",    country: "Russia",       tz: "Asia/Vladivostok" },
  { city: "Istanbul",       country: "Turkey",       tz: "Europe/Istanbul" },
  { city: "Ankara",         country: "Turkey",       tz: "Europe/Istanbul" },

  // Middle East
  { city: "Dubai",          country: "UAE",          tz: "Asia/Dubai" },
  { city: "Abu Dhabi",      country: "UAE",          tz: "Asia/Dubai" },
  { city: "Doha",           country: "Qatar",        tz: "Asia/Qatar" },
  { city: "Riyadh",         country: "Saudi Arabia", tz: "Asia/Riyadh" },
  { city: "Jeddah",         country: "Saudi Arabia", tz: "Asia/Riyadh" },
  { city: "Mecca",          country: "Saudi Arabia", tz: "Asia/Riyadh" },
  { city: "Kuwait City",    country: "Kuwait",       tz: "Asia/Kuwait" },
  { city: "Manama",         country: "Bahrain",      tz: "Asia/Bahrain" },
  { city: "Muscat",         country: "Oman",         tz: "Asia/Muscat" },
  { city: "Tel Aviv",       country: "Israel",       tz: "Asia/Jerusalem" },
  { city: "Jerusalem",      country: "Israel",       tz: "Asia/Jerusalem" },
  { city: "Amman",          country: "Jordan",       tz: "Asia/Amman" },
  { city: "Beirut",         country: "Lebanon",      tz: "Asia/Beirut" },
  { city: "Damascus",       country: "Syria",        tz: "Asia/Damascus" },
  { city: "Baghdad",        country: "Iraq",         tz: "Asia/Baghdad" },
  { city: "Tehran",         country: "Iran",         tz: "Asia/Tehran" },
  { city: "Kabul",          country: "Afghanistan",  tz: "Asia/Kabul" },

  // Africa
  { city: "Cairo",          country: "Egypt",        tz: "Africa/Cairo" },
  { city: "Alexandria",     country: "Egypt",        tz: "Africa/Cairo" },
  { city: "Lagos",          country: "Nigeria",      tz: "Africa/Lagos" },
  { city: "Abuja",          country: "Nigeria",      tz: "Africa/Lagos" },
  { city: "Nairobi",        country: "Kenya",        tz: "Africa/Nairobi" },
  { city: "Johannesburg",   country: "South Africa", tz: "Africa/Johannesburg" },
  { city: "Cape Town",      country: "South Africa", tz: "Africa/Johannesburg" },
  { city: "Pretoria",       country: "South Africa", tz: "Africa/Johannesburg" },
  { city: "Durban",         country: "South Africa", tz: "Africa/Johannesburg" },
  { city: "Casablanca",     country: "Morocco",      tz: "Africa/Casablanca" },
  { city: "Rabat",          country: "Morocco",      tz: "Africa/Casablanca" },
  { city: "Algiers",        country: "Algeria",      tz: "Africa/Algiers" },
  { city: "Tunis",          country: "Tunisia",      tz: "Africa/Tunis" },
  { city: "Tripoli",        country: "Libya",        tz: "Africa/Tripoli" },
  { city: "Addis Ababa",    country: "Ethiopia",     tz: "Africa/Addis_Ababa" },
  { city: "Accra",          country: "Ghana",        tz: "Africa/Accra" },
  { city: "Dakar",          country: "Senegal",      tz: "Africa/Dakar" },
  { city: "Kinshasa",       country: "DR Congo",     tz: "Africa/Kinshasa" },
  { city: "Khartoum",       country: "Sudan",        tz: "Africa/Khartoum" },
  { city: "Dar es Salaam",  country: "Tanzania",     tz: "Africa/Dar_es_Salaam" },
  { city: "Kampala",        country: "Uganda",       tz: "Africa/Kampala" },

  // South Asia
  { city: "Mumbai",         country: "India",        tz: "Asia/Kolkata" },
  { city: "Delhi",          country: "India",        tz: "Asia/Kolkata" },
  { city: "New Delhi",      country: "India",        tz: "Asia/Kolkata" },
  { city: "Bengaluru",      country: "India",        tz: "Asia/Kolkata" },
  { city: "Bangalore",      country: "India",        tz: "Asia/Kolkata" },
  { city: "Chennai",        country: "India",        tz: "Asia/Kolkata" },
  { city: "Kolkata",        country: "India",        tz: "Asia/Kolkata" },
  { city: "Hyderabad",      country: "India",        tz: "Asia/Kolkata" },
  { city: "Pune",           country: "India",        tz: "Asia/Kolkata" },
  { city: "Ahmedabad",      country: "India",        tz: "Asia/Kolkata" },
  { city: "Jaipur",         country: "India",        tz: "Asia/Kolkata" },
  { city: "Lucknow",        country: "India",        tz: "Asia/Kolkata" },
  { city: "Goa",            country: "India",        tz: "Asia/Kolkata" },
  { city: "Karachi",        country: "Pakistan",     tz: "Asia/Karachi" },
  { city: "Lahore",         country: "Pakistan",     tz: "Asia/Karachi" },
  { city: "Islamabad",      country: "Pakistan",     tz: "Asia/Karachi" },
  { city: "Dhaka",          country: "Bangladesh",   tz: "Asia/Dhaka" },
  { city: "Colombo",        country: "Sri Lanka",    tz: "Asia/Colombo" },
  { city: "Kathmandu",      country: "Nepal",        tz: "Asia/Kathmandu" },
  { city: "Thimphu",        country: "Bhutan",       tz: "Asia/Thimphu" },
  { city: "Malé",           country: "Maldives",     tz: "Indian/Maldives" },

  // Southeast Asia
  { city: "Bangkok",        country: "Thailand",     tz: "Asia/Bangkok" },
  { city: "Chiang Mai",     country: "Thailand",     tz: "Asia/Bangkok" },
  { city: "Phuket",         country: "Thailand",     tz: "Asia/Bangkok" },
  { city: "Jakarta",        country: "Indonesia",    tz: "Asia/Jakarta" },
  { city: "Bali",           country: "Indonesia",    tz: "Asia/Makassar" },
  { city: "Manila",         country: "Philippines",  tz: "Asia/Manila" },
  { city: "Cebu",           country: "Philippines",  tz: "Asia/Manila" },
  { city: "Singapore",      country: "Singapore",    tz: "Asia/Singapore" },
  { city: "Kuala Lumpur",   country: "Malaysia",     tz: "Asia/Kuala_Lumpur" },
  { city: "Penang",         country: "Malaysia",     tz: "Asia/Kuala_Lumpur" },
  { city: "Ho Chi Minh City", country: "Vietnam",    tz: "Asia/Ho_Chi_Minh" },
  { city: "Saigon",         country: "Vietnam",      tz: "Asia/Ho_Chi_Minh" },
  { city: "Hanoi",          country: "Vietnam",      tz: "Asia/Ho_Chi_Minh" },
  { city: "Yangon",         country: "Myanmar",      tz: "Asia/Yangon" },
  { city: "Phnom Penh",     country: "Cambodia",     tz: "Asia/Phnom_Penh" },
  { city: "Vientiane",      country: "Laos",         tz: "Asia/Vientiane" },

  // East Asia
  { city: "Tokyo",          country: "Japan",        tz: "Asia/Tokyo" },
  { city: "Osaka",          country: "Japan",        tz: "Asia/Tokyo" },
  { city: "Kyoto",          country: "Japan",        tz: "Asia/Tokyo" },
  { city: "Yokohama",       country: "Japan",        tz: "Asia/Tokyo" },
  { city: "Sapporo",        country: "Japan",        tz: "Asia/Tokyo" },
  { city: "Fukuoka",        country: "Japan",        tz: "Asia/Tokyo" },
  { city: "Seoul",          country: "South Korea",  tz: "Asia/Seoul" },
  { city: "Busan",          country: "South Korea",  tz: "Asia/Seoul" },
  { city: "Pyongyang",      country: "North Korea",  tz: "Asia/Pyongyang" },
  { city: "Beijing",        country: "China",        tz: "Asia/Shanghai" },
  { city: "Shanghai",       country: "China",        tz: "Asia/Shanghai" },
  { city: "Guangzhou",      country: "China",        tz: "Asia/Shanghai" },
  { city: "Shenzhen",       country: "China",        tz: "Asia/Shanghai" },
  { city: "Chengdu",        country: "China",        tz: "Asia/Shanghai" },
  { city: "Chongqing",      country: "China",        tz: "Asia/Shanghai" },
  { city: "Xi'an",          country: "China",        tz: "Asia/Shanghai" },
  { city: "Hangzhou",       country: "China",        tz: "Asia/Shanghai" },
  { city: "Wuhan",          country: "China",        tz: "Asia/Shanghai" },
  { city: "Hong Kong",      country: "Hong Kong",    tz: "Asia/Hong_Kong" },
  { city: "Macau",          country: "Macau",        tz: "Asia/Macau" },
  { city: "Taipei",         country: "Taiwan",       tz: "Asia/Taipei" },
  { city: "Kaohsiung",      country: "Taiwan",       tz: "Asia/Taipei" },
  { city: "Ulaanbaatar",    country: "Mongolia",     tz: "Asia/Ulaanbaatar" },

  // Central Asia
  { city: "Almaty",         country: "Kazakhstan",   tz: "Asia/Almaty" },
  { city: "Astana",         country: "Kazakhstan",   tz: "Asia/Almaty" },
  { city: "Tashkent",       country: "Uzbekistan",   tz: "Asia/Tashkent" },
  { city: "Baku",           country: "Azerbaijan",   tz: "Asia/Baku" },
  { city: "Tbilisi",        country: "Georgia",      tz: "Asia/Tbilisi" },
  { city: "Yerevan",        country: "Armenia",      tz: "Asia/Yerevan" },

  // Oceania
  { city: "Sydney",         country: "Australia",    tz: "Australia/Sydney" },
  { city: "Melbourne",      country: "Australia",    tz: "Australia/Melbourne" },
  { city: "Brisbane",       country: "Australia",    tz: "Australia/Brisbane" },
  { city: "Perth",          country: "Australia",    tz: "Australia/Perth" },
  { city: "Adelaide",       country: "Australia",    tz: "Australia/Adelaide" },
  { city: "Canberra",       country: "Australia",    tz: "Australia/Sydney" },
  { city: "Hobart",         country: "Australia",    tz: "Australia/Hobart" },
  { city: "Darwin",         country: "Australia",    tz: "Australia/Darwin" },
  { city: "Auckland",       country: "New Zealand",  tz: "Pacific/Auckland" },
  { city: "Wellington",     country: "New Zealand",  tz: "Pacific/Auckland" },
  { city: "Christchurch",   country: "New Zealand",  tz: "Pacific/Auckland" },
  { city: "Suva",           country: "Fiji",         tz: "Pacific/Fiji" },
  { city: "Port Moresby",   country: "Papua New Guinea", tz: "Pacific/Port_Moresby" },
];

// Case-insensitive substring search across city + country.
// Sorts so city-name matches rank above country-only matches.
export function searchCities(query: string, limit = 12): CityEntry[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const cityMatches: CityEntry[] = [];
  const countryMatches: CityEntry[] = [];
  for (const e of CITIES) {
    const cityLower = e.city.toLowerCase();
    const countryLower = e.country.toLowerCase();
    if (cityLower.includes(q)) {
      cityMatches.push(e);
    } else if (countryLower.includes(q)) {
      countryMatches.push(e);
    }
    if (cityMatches.length >= limit) break;
  }
  // Boost prefix matches to the front of cityMatches.
  cityMatches.sort((a, b) => {
    const ap = a.city.toLowerCase().startsWith(q) ? 0 : 1;
    const bp = b.city.toLowerCase().startsWith(q) ? 0 : 1;
    if (ap !== bp) return ap - bp;
    return a.city.localeCompare(b.city);
  });
  return [...cityMatches, ...countryMatches].slice(0, limit);
}
