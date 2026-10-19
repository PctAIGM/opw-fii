// Compatible with SubConverter Flutter: return the complete config object.
function main(config) {
    config.rules = config.rules || [];
    config.rules.unshift('DOMAIN,example.com,DIRECT');
    return config;
}
