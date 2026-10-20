// Временные данные витрины. Позже заменяются выгрузкой из CMS / базы мокапов клиента.
export type Product = {
  slug: string;
  name: string;
  category: string;
  min: number;
  methods: string[];
};

export const products: Product[] = [
  { slug: "hoodie", name: "Худи", category: "Одежда", min: 50, methods: ["Вышивка", "Шелкография", "DTG"] },
  { slug: "tshirt", name: "Футболка", category: "Одежда", min: 50, methods: ["Шелкография", "DTG", "Термотрансфер"] },
  { slug: "sweatshirt", name: "Свитшот", category: "Одежда", min: 50, methods: ["Вышивка", "Шелкография"] },
  { slug: "cap", name: "Кепка", category: "Аксессуары", min: 50, methods: ["Вышивка", "Нашивка"] },
  { slug: "shopper", name: "Шоппер", category: "Аксессуары", min: 50, methods: ["Шелкография", "DTG"] },
  { slug: "box", name: "Подарочный бокс", category: "Наборы", min: 50, methods: ["Тиснение", "Печать"] },
];
